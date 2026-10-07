#!/usr/bin/env python3
"""
Instrumentation harness for webrtc_sender_Now_latency_probe.py.

Loads the real sender as a module, wraps WebRTCSender.on_video_buffer, and
reports:
  - whether the source pad probe is entered at all
  - the exact PTS the source probe sees
  - any exception the real callback raises (which PyGObject would otherwise
    swallow into stderr)

The real sender file is NOT modified.

Run:  python3 -u tools/sender_patch_probe.py [seconds]
"""

import importlib.util
import pathlib
import sys
import traceback

RUN_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 12

HERE = pathlib.Path(__file__).resolve().parent.parent
TARGET = HERE / "webrtc_sender_Now_latency_probe.py"

spec = importlib.util.spec_from_file_location("sender_mod", TARGET)
sender_mod = importlib.util.module_from_spec(spec)
sys.modules["sender_mod"] = sender_mod
spec.loader.exec_module(sender_mod)

Gst = sender_mod.Gst

stats = {"video_calls": 0, "video_errors": 0, "first_pts": None, "last_pts": None}

original_video_probe = sender_mod.WebRTCSender.on_video_buffer


def instrumented_video_probe(self, pad, info):
    stats["video_calls"] += 1

    buffer = info.get_buffer()
    pts = buffer.pts if buffer is not None else None

    if stats["first_pts"] is None:
        stats["first_pts"] = pts

    stats["last_pts"] = pts

    if stats["video_calls"] <= 5:
        print(
            f"[PATCH] on_video_buffer call #{stats['video_calls']} pts={pts}",
            flush=True,
        )

    try:
        return original_video_probe(self, pad, info)
    except Exception:
        stats["video_errors"] += 1
        if stats["video_errors"] <= 3:
            print("[PATCH] EXCEPTION inside on_video_buffer:", flush=True)
            traceback.print_exc()
        return Gst.PadProbeReturn.OK


sender_mod.WebRTCSender.on_video_buffer = instrumented_video_probe


def report():
    print("=== PATCH REPORT ===", flush=True)
    print(f"on_video_buffer calls : {stats['video_calls']}", flush=True)
    print(f"exceptions            : {stats['video_errors']}", flush=True)
    print(f"first pts seen        : {stats['first_pts']}", flush=True)
    print(f"last  pts seen        : {stats['last_pts']}", flush=True)
    return False


sender = sender_mod.WebRTCSender()

sender_mod.GLib.timeout_add_seconds(RUN_SECONDS, report)

try:
    sender.start()
except KeyboardInterrupt:
    pass
finally:
    report()
    sender.stop()
