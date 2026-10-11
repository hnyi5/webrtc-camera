#!/usr/bin/env python3

import asyncio
import pathlib
import hmac
import http
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

from websockets.datastructures import Headers
from websockets.http11 import Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import websockets

import config


# Connections, by role.
#
# The sender is the one that produces offers and frame mappings; viewers are
# browsers.  Keeping them apart is what stops a second browser -- or the dying
# socket of a page that just reloaded -- from being sent an offer it will
# answer, confusing the session.
senders = set()

# Most recently connected viewer last.  The sender talks only to the newest, so
# a reload's leftover socket receives nothing.
viewers = []

# Roles, carried from process_request to handler.
#
# Not an attribute on the connection: ServerConnection is slots-based, so
# `connection.dsh_role = ...` raises inside process_request and refuses every
# handshake.  The two callbacks receive the same object, so id() is a safe key
# for the length of the handshake.
pending_roles = {}


def role_from_path(path):
    """Who is connecting.  Anything that is not the sender is a viewer."""
    values = parse_qs(urlsplit(path or "").query).get("role")
    role = values[0] if values else "viewer"
    return "sender" if role == "sender" else "viewer"


# How long a single relayed message may take before the target is dropped.
#
# The relay runs inside the SENDER'S or VIEWER'S receive loop, so a send that
# never completes does not merely delay one message: it stops the server reading
# from that client, which stops the client's own sends, which stalls the
# session.  Dropping a target that cannot keep up is the cheap, local answer.
SEND_TIMEOUT_SECONDS = 5


# The two files the page needs, and their content types.
#
# Served from this port on purpose: the page and the WebSocket then share one
# origin, which is what stops a half-working deployment where the page loads
# from one address and every WebSocket to another address dies with code 1006.
#
# No token is required for these.  The page carries the token in its own URL
# anyway, and a <script src="config.js"> cannot attach one -- requiring it would
# silently strip the page's configuration, which is a confusing way to fail.
STATIC_FILES = {
    "/index_latency.html": ("index_latency.html", "text/html; charset=utf-8"),
    "/config.js": ("config.js", "application/javascript; charset=utf-8"),
}


def static_response(path):
    """The page file for this path, or None if it is not one of them."""
    entry = STATIC_FILES.get(path)

    if entry is None:
        return None

    name, content_type = entry
    file_path = pathlib.Path(__file__).resolve().parent / name

    if not file_path.is_file():
        return Response(
            404,
            "Not Found",
            Headers({"Content-Type": "text/plain; charset=utf-8"}),
            ("not found: " + name + "\n").encode(),
        )

    body = file_path.read_bytes()

    return Response(
        200,
        "OK",
        Headers(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                # Regenerated whenever config.py changes, so a cached copy
                # would quietly keep talking to an old address.
                "Cache-Control": "no-store",
            }
        ),
        body,
    )


def token_from_path(path):
    """Pull ?token=... out of the handshake request path."""
    values = parse_qs(urlsplit(path or "").query).get("token")
    return values[0] if values else None


async def process_request(connection, request):
    """Check the shared token before the WebSocket upgrade completes.

    With no token configured, authentication is disabled.  That is fine on a
    trusted LAN and unacceptable in public, so main() shouts about it.

    Rejecting here rather than inside the handler means a client with a bad
    token sees a clean 401, not a connection that opens and instantly closes.
    """
    path = urlsplit(request.path or "").path

    # Page files first: a plain GET for one of them is answered here, and
    # anything else -- the WebSocket upgrade included -- falls through.
    page = static_response(path)

    if page is not None:
        return page

    role = role_from_path(request.path)

    if config.ACCESS_TOKEN:
        token = token_from_path(request.path)

        # Constant time: a plain == lets an attacker recover the token one byte
        # at a time by measuring how long the comparison takes.
        if token is None or not hmac.compare_digest(token, config.ACCESS_TOKEN):
            print("[SIGNALING] rejected connection: bad or missing token")
            return connection.respond(
                http.HTTPStatus.UNAUTHORIZED, "unauthorized\n"
            )

    pending_roles[id(connection)] = role

    return None


async def send_turn_credentials(websocket):
    """Hand a client its own time-limited TURN credentials.

    Sent BEFORE the socket joins the relay lists, so it is structurally impossible
    a relayed offer to arrive first.  That matters: the browser creates its peer
    connection before the socket opens, so an offer that got there first would
    produce an answer with no relay candidates, and the relay would then never
    be used -- silently.

    The secret behind these values never leaves the server.
    """
    credentials = config.turn_credentials()

    if credentials is None:
        return

    await websocket.send(
        json.dumps({"type": "turn-credentials", **credentials})
    )


async def deliver(target, payload):
    """Send, or drop the target.

    An abandoned socket stays in our bookkeeping until the server notices, so
    send() can raise.  Letting that propagate would end the handler -- and when
    the target is a viewer, the handler that dies is the SENDER's, taking the
    whole session down with it.  Dropping the target is the cheap answer.
    """
    try:
        # Bounded on purpose.  A client that has stopped reading -- the sender
        # under a busy GLib loop, most likely -- would otherwise block this
        # viewer's receive loop forever, and with it the server's reads from
        # that viewer, and with those the viewer's own sends.
        await asyncio.wait_for(target.send(payload), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except Exception as error:  # noqa: BLE001
        print(
            "[SIGNALING] dropping a client that refused a send:",
            type(error).__name__,
        )
        senders.discard(target)
        if target in viewers:
            viewers.remove(target)
        return False


async def handler(websocket):
    role = pending_roles.pop(id(websocket), "viewer")

    await send_turn_credentials(websocket)

    if role == "sender":
        senders.add(websocket)
    else:
        viewers.append(websocket)

    print(f"[SIGNALING] Client connected ({role})")

    try:
        async for message in websocket:
            print("[SIGNALING] Message received")

            data = json.loads(message)
            payload = json.dumps(data)

            if role == "sender":
                # Only the newest viewer.  A reload leaves the previous socket
                # in the list for a moment, and sending to both would hand the
                # sender two answers to one offer.
                if viewers:
                    await deliver(viewers[-1], payload)
            else:
                for target in list(senders):
                    await deliver(target, payload)

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        senders.discard(websocket)
        if websocket in viewers:
            viewers.remove(websocket)
        print(f"[SIGNALING] Client disconnected ({role})")


async def main():
    print(
        "[SIGNALING] WebSocket server starting on "
        f"{config.SIGNALING_HOST}:{config.SIGNALING_PORT}"
    )

    credentials = config.turn_credentials()
    if credentials is None:
        print("[SIGNALING] no TURN credentials to issue (TURN not configured)")
    else:
        print(
            "[SIGNALING] issuing time-limited TURN credentials, ttl "
            f"{credentials['ttl']}s, {len(credentials['urls'])} url(s)"
        )

    if config.ACCESS_TOKEN:
        print("[SIGNALING] access token required (?token=...)")
    else:
        print("[SIGNALING] " + "*" * 62)
        print("[SIGNALING] WARNING: DSH_ACCESS_TOKEN is not set.")
        print("[SIGNALING]          Any client can connect.  Acceptable only on")
        print("[SIGNALING]          a trusted LAN -- set it before exposing this")
        print("[SIGNALING]          server to the internet.")
        print("[SIGNALING] " + "*" * 62)

    async with websockets.serve(
        handler,
        config.SIGNALING_HOST,
        config.SIGNALING_PORT,
        process_request=process_request,
    ):
        print("[SIGNALING] WebSocket server ready")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
