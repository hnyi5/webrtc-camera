#!/usr/bin/env python3

import asyncio
import hmac
import http
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import websockets

import config


clients = set()


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
    if not config.ACCESS_TOKEN:
        return None

    token = token_from_path(request.path)

    # Constant time: a plain == lets an attacker recover the token one byte at
    # a time by measuring how long the comparison takes.
    if token is not None and hmac.compare_digest(token, config.ACCESS_TOKEN):
        return None

    print("[SIGNALING] rejected connection: bad or missing token")
    return connection.respond(http.HTTPStatus.UNAUTHORIZED, "unauthorized\n")


async def send_turn_credentials(websocket):
    """Hand a client its own time-limited TURN credentials.

    Sent BEFORE the socket joins `clients`, so it is structurally impossible for
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


async def handler(websocket):
    await send_turn_credentials(websocket)

    clients.add(websocket)

    print("[SIGNALING] Client connected")

    try:
        async for message in websocket:
            print("[SIGNALING] Message received")

            data = json.loads(message)

            # 把消息转发给其他客户端
            for client in clients:
                if client != websocket:
                    await client.send(json.dumps(data))

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        clients.discard(websocket)
        print("[SIGNALING] Client disconnected")


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
