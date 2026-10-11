#!/usr/bin/env bash
#
# Restart the local stack: signalling server, page server, sender.
#
# Why this is a script rather than an inline command: a command line that
# mentions run_sender_loop.sh makes `pgrep -f run_sender_loop.sh` match the
# shell running it, and that shell then kills itself halfway through -- which
# shows up as a truncated SSH session and a half-started stack.  Patterns in a
# file never appear in any command line, so this version is safe.
#
# Usage:
#     bash tools/restart_stack.sh                 # signalling + page + sender
#     bash tools/restart_stack.sh --no-sender     # signalling + page only
#
# Environment (all optional; see config.py):
#     DSH_ACCESS_TOKEN   shared token clients must present (empty = LAN mode)
#     DSH_VIDEO_SOURCE   v4l2src (default) or videotestsrc
#     DSH_PAGE_PORT      default 8000
#
# Logs: /tmp/sig.log, /tmp/page.log, /tmp/sender.log

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PAGE_PORT="${DSH_PAGE_PORT:-8000}"
START_SENDER=1
if [ "${1:-}" = "--no-sender" ]; then
    START_SENDER=0
fi

log() { printf '%s\n' "$*"; }

self=$$

log "stopping existing processes"

for pattern in \
    "run_sender_loop.sh signaling_server.py" \
    "run_sender_loop.sh$" \
    "webrtc_sender_Now_latency_probe.py"
do
    for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
        [ "$pid" = "$self" ] && continue
        kill "$pid" 2>/dev/null || true
    done
done

sleep 2

log "starting the signalling server"
setsid nohup ./run_sender_loop.sh signaling_server.py </dev/null >/tmp/sig.log 2>&1 &
sleep 3

if ss -tln | grep -q ':8765 '; then
    # -a: a previous crashed run can leave binary bytes in the log, and grep
    # then refuses to print anything useful ("binary file matches").
    grep -a -E 'access token required|WARNING|server ready' /tmp/sig.log | head -4
else
    log "  !! not listening; last lines of /tmp/sig.log:"
    tail -15 /tmp/sig.log 2>/dev/null
fi

log "starting the page server (serve_page.py, serves only the page and config.js)"
if ! ss -tln | grep -q ":$PAGE_PORT "; then
    setsid nohup python3 serve_page.py "$PAGE_PORT" </dev/null >/tmp/page.log 2>&1 &
    sleep 2
fi

if ss -tln | grep -q ":$PAGE_PORT "; then
    log "  page on :$PAGE_PORT"
else
    log "  !! page server not listening; last lines of /tmp/page.log:"
    tail -15 /tmp/page.log 2>/dev/null
fi

if [ "$START_SENDER" = "1" ]; then
    if [ -n "${DSH_ACCESS_TOKEN:-}" ]; then
        log "starting the sender (token configured)"
    else
        log "starting the sender (no token -- LAN mode)"
    fi

    setsid nohup ./run_sender_loop.sh </dev/null >/tmp/sender.log 2>&1 &
    sleep 8

    grep -a -E 'signalling url|Pipeline: paused -> playing|SDP offer sent' \
        /tmp/sender.log | tail -3
fi

log ""
log "state"

ss -tln | grep -E ":$PAGE_PORT |:8765 " || log "  !! nothing listening"

if pgrep -f "webrtc_sender_Now_latency_probe.py" >/dev/null; then
    log "  sender : running"
else
    log "  sender : not running"
fi

if [ -n "${DSH_ACCESS_TOKEN:-}" ]; then
    log "  token  : set (${#DSH_ACCESS_TOKEN} chars) -- clients need ?token=..."
else
    log "  token  : NOT set -- any client may connect"
fi
