#!/usr/bin/env python3
"""
How long does a WebSocket through the public tunnel actually survive?

The phone's failures could be the phone (a VPN, a filter, a quirk) or the tunnel
itself.  Guessing between them has already cost several rounds, so measure it:

    open a real WebSocket through the public URL, keep sending, and report how
    long it lasts.

If a WebSocket from this machine dies at thirty seconds too, the phone is
innocent and the tunnel is the problem -- and the cloudflared log already hints
at that, with repeated "TLS handshake with edge ... i/o timeout" followed by a
re-registration on a different edge address.

    python3 tools/tunnel_probe.py https://host.trycloudflare.com TOKEN [rounds]
"""

import asyncio
import json
import sys
import time

import websockets

HOLD_SECONDS = 75


def ws_url(base, token):
    base = base.strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + "/?token=" + token + "&role=viewer"


async def one_round(number, url):
    started = time.monotonic()
    print("  round {0}: connecting...".format(number), flush=True)

    try:
        async with websockets.connect(url, open_timeout=25) as socket:
            opened = time.monotonic() - started
            print("  round {0}: OPEN after {1:.1f}s".format(number, opened), flush=True)

            while True:
                await asyncio.wait_for(
                    socket.send(json.dumps({"type": "tunnel_probe"})), timeout=8
                )

                elapsed = time.monotonic() - started

                if elapsed >= HOLD_SECONDS:
                    print(
                        "  round {0}: SURVIVED {1}s".format(number, HOLD_SECONDS),
                        flush=True,
                    )
                    return True

                if int(elapsed) % 15 == 0:
                    print("  round {0}: alive at {1:.0f}s".format(number, elapsed),
                          flush=True)

                await asyncio.sleep(2)

    except Exception as error:  # noqa: BLE001
        died = time.monotonic() - started
        print(
            "  round {0}: DIED after {1:.1f}s -- {2}: {3}".format(
                number, died, type(error).__name__, error
            ),
            flush=True,
        )
        return False


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    base, token = sys.argv[1], sys.argv[2]
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    url = ws_url(base, token)

    print("probing " + url.replace("token=" + token, "token=***"))
    print("holding each connection up to {0}s".format(HOLD_SECONDS))
    print()

    results = []
    for number in range(1, rounds + 1):
        results.append(await one_round(number, url))
        await asyncio.sleep(2)

    print()
    survived = sum(1 for item in results if item)
    print("{0}/{1} connections survived {2}s".format(
        survived, len(results), HOLD_SECONDS))

    if survived == 0:
        print()
        print("Every attempt died.  The tunnel is the problem, not the phone.")

    return 0 if survived == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
