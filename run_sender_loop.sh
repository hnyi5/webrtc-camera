#!/bin/bash
#
# Supervisor for the WebRTC sender.
#
# The sender exits when its signaling WebSocket closes, because nothing can be
# measured without that link and neither side can renegotiate over a closed
# socket.  This loop brings it back, and every restart produces a fresh SDP
# offer -- which is what re-establishes the video after the browser page
# reloads itself.
#
# Usage:  ./run_sender_loop.sh [sender-script]

set -u

cd "$(dirname "$0")" || exit 1

SENDER="${1:-webrtc_sender_Now_latency_probe.py}"

# A sender that cannot start at all fails within a second, over and over.  A
# flat 2 s restart then turns a clear configuration problem into a log flood and
# a busy CPU, and the only way out is killing this loop by hand -- which is
# exactly what happened when the camera moved from /dev/video0 to /dev/video1.
#
# So: back off on repeated fast failures, and say something useful after a few
# in a row.  A run that lasts is treated as genuine, and the delay resets.
MIN_DELAY=2
MAX_DELAY=30
GOOD_RUN_SECONDS=20
HINT_AFTER=3

delay=$MIN_DELAY
fast_failures=0

hint() {
    echo "[SUPERVISOR] ------------------------------------------------------------"
    echo "[SUPERVISOR] $SENDER has failed $fast_failures times in a row in under"
    echo "[SUPERVISOR] ${GOOD_RUN_SECONDS}s.  The usual causes, in order:"
    echo "[SUPERVISOR]   1. camera device -- /dev/videoN moves when it re-enumerates."
    echo "[SUPERVISOR]      Check:  python3 config.py     (it names the by-id path)"
    echo "[SUPERVISOR]   2. camera busy or unplugged     fuser -v /dev/video0"
    echo "[SUPERVISOR]   3. signalling port held by a stale process"
    echo "[SUPERVISOR]      Check:  ss -tlnp | grep :8765"
    echo "[SUPERVISOR] ------------------------------------------------------------"
}

while true; do

    echo "[SUPERVISOR] starting $SENDER"

    started=$(date +%s)
    python3 -u "$SENDER"
    code=$?
    ran=$(( $(date +%s) - started ))

    if [ "$ran" -ge "$GOOD_RUN_SECONDS" ]; then
        delay=$MIN_DELAY
        fast_failures=0
    else
        fast_failures=$(( fast_failures + 1 ))
        delay=$(( delay * 2 ))
        if [ "$delay" -gt "$MAX_DELAY" ]; then
            delay=$MAX_DELAY
        fi
        if [ "$fast_failures" -eq "$HINT_AFTER" ]; then
            hint
        fi
    fi

    echo "[SUPERVISOR] $SENDER exited (code $code) after ${ran}s," \
         "restarting in ${delay}s"

    sleep "$delay"

done
