#!/usr/bin/env python3
"""
Single source of truth for every deployment-specific value.

Before this file existed these values were hard-coded across the sender, the
signalling server, the browser page and several tools.  Porting the project
meant finding and editing all of them, and a missed one produced a confusing
runtime failure instead of an error.

Precedence, highest first:

    1. environment variable  (DSH_...)
    2. the value in this file
    3. the built-in default

So a deployment overrides things without editing tracked code:

    DSH_VIDEO_DEVICE=/dev/video2 \
    DSH_SIGNALING_URL=ws://183.198.108.95:8765 \
        python3 webrtc_sender_Now_latency_probe.py

The browser page cannot read this file, so its values are written into
`config.js` by tools/make_page_config.py.  That keeps one place to edit:

    python3 tools/make_page_config.py        # regenerate config.js

Print the effective configuration with:

    python3 config.py
"""

import os


def _str(name, default):
    return os.environ.get(name, default)


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


# ---------------------------------------------------------------------------
# video source
# ---------------------------------------------------------------------------
# "v4l2src"      real camera
# "videotestsrc" GStreamer test pattern.  Needs no camera, which makes it
#                possible to exercise the whole network path - signalling,
#                STUN/ICE, DTLS, SRTP, the browser - on a machine that has no
#                camera attached at all.
VIDEO_SOURCE = _str("DSH_VIDEO_SOURCE", "v4l2src")
VIDEO_DEVICE = _str("DSH_VIDEO_DEVICE", "/dev/video0")
VIDEO_CAPS = _str(
    "DSH_VIDEO_CAPS", "image/jpeg,width=1920,height=1080,framerate=30/1"
)

# Used by the videotestsrc branch only.
VIDEO_WIDTH = _int("DSH_VIDEO_WIDTH", 1920)
VIDEO_HEIGHT = _int("DSH_VIDEO_HEIGHT", 1080)
VIDEO_FPS = _int("DSH_VIDEO_FPS", 30)

# ---------------------------------------------------------------------------
# signalling
# ---------------------------------------------------------------------------
# The sender connects to SIGNALING_URL.  SIGNALING_HOST/PORT are what the
# signalling server binds to.
SIGNALING_URL = _str("DSH_SIGNALING_URL", "ws://127.0.0.1:8765")
SIGNALING_HOST = _str("DSH_SIGNALING_HOST", "0.0.0.0")
SIGNALING_PORT = _int("DSH_SIGNALING_PORT", 8765)

# ---------------------------------------------------------------------------
# encoder
# ---------------------------------------------------------------------------
BITRATE_KBPS = _int("DSH_BITRATE_KBPS", 4000)
KEY_INT_MAX = _int("DSH_KEY_INT_MAX", 30)
SPEED_PRESET = _str("DSH_SPEED_PRESET", "ultrafast")

# ---------------------------------------------------------------------------
# ICE servers (consumed by config.js for the browser page)
# ---------------------------------------------------------------------------
# Measured on the current deployment network: endpoint-independent mapping
# (cone NAT), so STUN alone is enough and TURN is empty.  See
# tools/stun_probe.py and docs/TECHNICAL_NOTES.md section 32.
# Comma-separated, so several servers can be listed for redundancy.  That
# matters on this network: stun.l.google.com intermittently fails DNS lookup
# (seen as STUN error 701 in the browser test), while stun.miwifi.com and
# stun.cloudflare.com answered reliably.  A single unreachable STUN server
# means no srflx candidate at all, which breaks the public-internet case.
STUN_URL = _str(
    "DSH_STUN_URL",
    "stun:stun.l.google.com:19302,stun:stun.miwifi.com:3478",
)
TURN_URL = _str("DSH_TURN_URL", "")
TURN_USERNAME = _str("DSH_TURN_USERNAME", "")
TURN_PASSWORD = _str("DSH_TURN_PASSWORD", "")

# ---------------------------------------------------------------------------
# tooling
# ---------------------------------------------------------------------------
PAGE_PORT = _int("DSH_PAGE_PORT", 8000)
CDP_HTTP = _str("DSH_CDP_HTTP", "http://127.0.0.1:9222")
TIME_SERVER_PORT = _int("DSH_TIME_SERVER_PORT", 9099)


def video_source_description():
    """The GStreamer fragment that produces *decoded* frames for the probe.

    Both branches end with frames ready for x264enc, so the timestamp probe
    sits at the same logical point ("the decoded frame") either way and the
    measured numbers stay comparable.
    """
    if VIDEO_SOURCE == "videotestsrc":
        return (
            "videotestsrc is-live=true pattern=smpte"
            " ! video/x-raw,format=I420"
            f",width={VIDEO_WIDTH},height={VIDEO_HEIGHT}"
            f",framerate={VIDEO_FPS}/1"
        )

    return (
        f"v4l2src device={VIDEO_DEVICE} io-mode=mmap"
        f" ! {VIDEO_CAPS}"
        " ! jpegdec"
    )


def ice_servers():
    """ICE server list in the shape RTCPeerConnection expects."""
    servers = []

    for url in (piece.strip() for piece in STUN_URL.split(",")):
        if url:
            servers.append({"urls": url})

    if TURN_URL:
        entry = {"urls": TURN_URL}
        if TURN_USERNAME:
            entry["username"] = TURN_USERNAME
        if TURN_PASSWORD:
            entry["credential"] = TURN_PASSWORD
        servers.append(entry)

    return servers


def describe():
    """Lines describing the effective configuration, for startup logging."""
    return [
        f"video source   : {VIDEO_SOURCE}",
        f"video device   : {VIDEO_DEVICE}",
        f"video caps     : {VIDEO_CAPS}",
        f"signalling url : {SIGNALING_URL}",
        f"signalling bind: {SIGNALING_HOST}:{SIGNALING_PORT}",
        f"encoder        : bitrate={BITRATE_KBPS} preset={SPEED_PRESET}"
        f" key-int-max={KEY_INT_MAX}",
        f"ice servers    : {ice_servers()}",
        f"page port      : {PAGE_PORT}",
    ]


if __name__ == "__main__":
    print("effective configuration")
    print("=" * 60)
    for line in describe():
        print("  " + line)
    print()
    print("gstreamer source fragment:")
    print("  " + video_source_description())
