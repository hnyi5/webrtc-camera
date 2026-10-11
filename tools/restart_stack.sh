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

is_ancestor() {
    # Walk up the parent chain from this script.  Anything found there is part
    # of the session that invoked us -- an ssh shell, a terminal, a CI job --
    # and must never be killed, whatever its command line happens to contain.
    local target="$1"
    local pid=$$

    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
        [ "$pid" = "$target" ] && return 0
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    done

    return 1
}

log "stopping existing processes"

# Two things matter here:
#
#   * the python children need killing in their own right -- killing the
#     supervising shell does not take them with it, and a surviving child keeps
#     the port, so the replacement silently fails to bind while the port check
#     still reports success;
#   * the patterns are anchored to the whole command line, so a shell that
#     merely mentions a name (an ssh command that also greps the sender's log)
#     is not mistaken for the process.
for pattern in \
    "^/bin/bash \./run_sender_loop\.sh signaling_server\.py$" \
    "^python3 -u signaling_server\.py$" \
    "^/bin/bash \./run_sender_loop\.sh$" \
    "^python3 -u webrtc_sender_Now_latency_probe\.py$"
do
    for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
        [ "$pid" = "$self" ] && continue

        if is_ancestor "$pid"; then
            log "  skipping $pid -- it is part of the session that started this"
            continue
        fi

        log "  stopping $pid ($(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null))"
        kill "$pid" 2>/dev/null || true
    done
done

sleep 2

# Wait for the port to actually come free.  Starting into a held port fails
# quietly from the outside: the old process answers, so everything looks fine.
for _ in $(seq 1 20); do
    if ! ss -tln | grep -q ':8765 '; then
        break
    fi
    sleep 0.5
done

if ss -tln | grep -q ':8765 '; then
    log "!! port 8765 is STILL held after killing everything:"
    ss -tlnp 2>/dev/null | grep ':8765 ' || true
    log "   the signalling server below will fail to bind.  Kill the holder first."
fi

log "starting the signalling server"
setsid nohup ./run_sender_loop.sh signaling_server.py </dev/null >/tmp/sig.log 2>&1 &
sleep 3

if ss -tln | grep -q ':8765 '; then
    # -a: a previous crashed run can leave binary bytes in the log, and grep
    # then refuses to print anything useful ("binary file matches").
    grep -a -E 'access token required|WARNING|server ready|issuing' /tmp/sig.log | head -5

    # Listening is not the same as *our* server listening.  Compare the log's
    # most recent start against the process that holds the port.
    running="$(pgrep -f 'signaling_server.py' | head -1)"
    if [ -n "$running" ]; then
        log "  signalling pid $running, started $(ps -o lstart= -p "$running" 2>/dev/null)"
    fi
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
