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

while true; do

    echo "[SUPERVISOR] starting $SENDER"

    python3 -u "$SENDER"

    echo "[SUPERVISOR] $SENDER exited (code $?), restarting in 2 s"

    sleep 2

done
