#!/usr/bin/env python3
"""
Verify the security layer, in two parts.

Part 1 -- TURN credential minting and signalling-URL plumbing.  Pure logic;
no network involved.

Part 2 -- live signalling authentication.  Starts a signalling server on a
scratch port and checks that the right token is accepted while a wrong one and
a missing one are refused.

    python3 tools/verify_security.py

Exit status is 0 only when every check passes, so it can gate a deployment.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_SECRET = "testsecret"
TEST_TOKEN = "tok3n-with/special+chars="
SCRATCH_PORT = 8799

failures = []


def check(label, condition, detail=""):
    mark = "  [ok]  " if condition else "  [!!]  "
    print(mark + label + (("   " + detail) if detail else ""))
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
print("=" * 72)
print("part 1: TURN credentials and signalling URL")
print("=" * 72)

os.environ["DSH_TURN_SECRET"] = TEST_SECRET
os.environ["DSH_TURN_URL"] = "turn:1.2.3.4:3478?transport=udp,stun:1.2.3.4:3478"
os.environ["DSH_ACCESS_TOKEN"] = TEST_TOKEN
os.environ["DSH_TURN_TTL_SECONDS"] = "3600"

import config  # noqa: E402  (environment set up above on purpose)

creds = config.turn_credentials(now=1000000000)
expected_username = "1000003600"
expected_credential = base64.b64encode(
    hmac.new(TEST_SECRET.encode(), expected_username.encode(), hashlib.sha1).digest()
).decode()

check("username is the expiry timestamp", creds["username"] == expected_username,
      creds["username"])
check("credential is base64(HMAC-SHA1(secret, username))",
      creds["credential"] == expected_credential)
check("ttl reported", creds["ttl"] == 3600)
check("both URLs split out of the comma list",
      creds["urls"] == ["turn:1.2.3.4:3478?transport=udp", "stun:1.2.3.4:3478"])
check("the secret itself is not in the result", TEST_SECRET not in str(creds))

later = config.turn_credentials(now=1000003600)
check("a later mint gets a different username",
      later["username"] != creds["username"])

uris = config.turn_server_uris(creds)
check("one TURN URI, credential inlined",
      len(uris) == 1 and uris[0].startswith("turn://"),
      uris[0] if uris else "none")

# base64's alphabet is A-Za-z0-9+/= -- the last three are URI-special, so the
# credential must appear escaped or the URI will be misparsed.
escaped_credential = (
    creds["credential"]
    .replace("+", "%2B")
    .replace("/", "%2F")
    .replace("=", "%3D")
)
check("credential is percent-escaped inside the TURN URI",
      all(escaped_credential in uri for uri in uris),
      uris[0] if uris else "none")
check("the stun: entry is not turned into a TURN URI",
      all("stun" not in uri for uri in uris), str(uris))
check("no query string (webrtcbin does not document one)",
      all("?" not in uri for uri in uris), str(uris))
check("webrtcbin gets no secret of ours",
      TEST_SECRET not in " ".join(uris))

url = config.signaling_url_with_token()
check("token appended to the signalling URL", "token=" in url, url)
carried = url.split("token=", 1)[1]
check("token is percent-escaped",
      "/" not in carried and "+" not in carried and "=" not in carried, carried)
check("round-trips back to the original",
      __import__("urllib.parse", fromlist=["unquote"]).unquote(carried) == TEST_TOKEN)

# The "no token configured" path has to run in a fresh interpreter, because
# config reads the environment at import time.
child_env = {k: v for k, v in os.environ.items() if k != "DSH_ACCESS_TOKEN"}
child = subprocess.run(
    [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, {0!r}); import config; "
        "print(config.signaling_url_with_token())".format(str(ROOT)),
    ],
    env=child_env,
    capture_output=True,
    text=True,
)
check("with no token configured the URL is left alone",
      child.stdout.strip() == "ws://127.0.0.1:8765", child.stdout.strip())


# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("part 2: live signalling authentication")
print("=" * 72)


async def probe(token):
    """Connect with the given token; return (accepted, detail).

    The token is percent-escaped here exactly as the real clients do it
    (quote() in the sender, encodeURIComponent() in the page).  Skipping that
    step makes parse_qs turn a "+" into a space, and the server then rejects a
    token that was in fact correct -- which is what happened the first time
    this test ran.
    """
    import urllib.parse
    import websockets

    url = "ws://127.0.0.1:{0}/".format(SCRATCH_PORT)
    if token is not None:
        url += "?token=" + urllib.parse.quote(token, safe="")

    try:
        socket = await websockets.connect(url)
    except Exception as error:  # noqa: BLE001
        return False, "handshake refused ({0})".format(type(error).__name__)

    try:
        # The server accepts the upgrade and then closes with 1008, so give it
        # a moment and look at the resulting state.
        await asyncio.sleep(0.4)
        state = getattr(socket, "state", None)
        name = getattr(state, "name", str(state))

        if name != "OPEN":
            return False, "closed by server ({0})".format(name)

        return True, "open"
    finally:
        try:
            await socket.close()
        except Exception:  # noqa: BLE001
            pass


async def main_live():
    server_env = dict(os.environ)
    server_env["DSH_ACCESS_TOKEN"] = TEST_TOKEN
    server_env["DSH_SIGNALING_PORT"] = str(SCRATCH_PORT)
    server_env["DSH_SIGNALING_HOST"] = "127.0.0.1"

    server = subprocess.Popen(
        # -u matters: without it Python block-buffers stdout when it is a pipe,
        # so terminating the server throws away its log and there is no way to
        # see why a connection was refused.
        [sys.executable, "-u", str(ROOT / "signaling_server.py")],
        cwd=str(ROOT),
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    await asyncio.sleep(1.5)

    try:
        accepted, detail = await probe(TEST_TOKEN)
        check("correct token accepted", accepted, detail)

        accepted, detail = await probe("wrong-token")
        check("wrong token refused", not accepted, detail)

        accepted, detail = await probe(None)
        check("missing token refused", not accepted, detail)
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

    check("server said it requires a token",
          "access token required" in (output or ""))
    check("rejections were logged",
          (output or "").count("rejected connection") >= 2,
          "{0} rejections".format((output or "").count("rejected connection")))


asyncio.run(main_live())


# ---------------------------------------------------------------------------
print()
print("=" * 72)
if failures:
    print("FAILED: {0} check(s)".format(len(failures)))
    for item in failures:
        print("  - " + item)
    sys.exit(1)

print("all checks passed")
sys.exit(0)
