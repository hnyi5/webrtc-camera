#!/usr/bin/env python3
"""
Check that the role-aware relay does what it is supposed to.

Run against its OWN signalling server on a scratch port, not the live one.  The
first version of this test connected to the running server, where the real
sender is busy pushing frame mappings to every viewer -- so "read the next
message" picked up a mapping instead of the probe, and two checks failed for a
reason that had nothing to do with the relay.

What it proves, because the previous design got all of it wrong:

  * a viewer's message reaches the sender;
  * the sender's message reaches the newest viewer;
  * an OLDER viewer receives none of the sender's messages -- that is the case
    that used to break a session, whether the extra socket was a second browser
    or the leftovers of a page that had just reloaded;
  * when the newest viewer goes away, the previous one takes over.

    python3 tools/verify_relay.py
"""

import asyncio
import os
import pathlib
import subprocess
import sys

import websockets

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = 8797
TOKEN = "relay-test-token"

failures = []


def check(label, condition, detail=""):
    print(("  [ok]  " if condition else "  [!!]  ") + label
          + (("   " + detail) if detail else ""))
    if not condition:
        failures.append(label)


def url(role):
    return f"ws://127.0.0.1:{PORT}/?token={TOKEN}&role={role}"


async def recv_matching(socket, wanted, timeout=2.0):
    """Wait for one of OUR message types, ignoring the chatter."""
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None

        import json
        kind = json.loads(raw).get("type")
        if kind == wanted:
            return kind


async def main():
    env = dict(os.environ, DSH_ACCESS_TOKEN=TOKEN, DSH_SIGNALING_PORT=str(PORT),
               DSH_SIGNALING_HOST="127.0.0.1")
    server = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "signaling_server.py")],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    await asyncio.sleep(1.5)

    try:
        sender = await websockets.connect(url("sender"))
        first = await websockets.connect(url("viewer"))
        second = await websockets.connect(url("viewer"))
        await asyncio.sleep(0.4)

        print("viewer -> sender")
        await first.send('{"type":"from_first_viewer"}')
        check("reaches the sender",
              await recv_matching(sender, "from_first_viewer") is not None)

        print()
        print("sender -> newest viewer")
        await sender.send('{"type":"to_newest"}')
        check("reaches the newest viewer",
              await recv_matching(second, "to_newest") is not None)

        print()
        print("the older viewer must get nothing from the sender")
        await sender.send('{"type":"only_newest"}')
        stray = await recv_matching(first, "only_newest", timeout=1.5)
        check("older viewer receives nothing", stray is None)

        print()
        print("newest viewer leaves -> the older one takes over")
        await second.close()
        await asyncio.sleep(0.5)
        await sender.send('{"type":"after_close"}')
        check("handover works",
              await recv_matching(first, "after_close") is not None)

        await sender.close()
        await first.close()
    finally:
        server.terminate()
        try:
            output = server.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            server.kill()
            output = server.communicate()[0]

    print()
    print("server log:")
    for line in (output or "").splitlines():
        print("    " + line)

    print()
    if failures:
        print("FAILED: {0} check(s)".format(len(failures)))
        for item in failures:
            print("  - " + item)
        return 1

    print("all relay checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
