#!/usr/bin/env python3
"""
GStreamer pipeline PTS diagnostic.

Purpose: find out, per element, whether a pad probe fires at all and what
PTS/DTS/duration the buffers carry.  Written to diagnose why
webrtc_sender_Now_latency_probe.py reports "No source timestamp" for every
RTP packet.

Run:  python3 tools/pts_diag.py [seconds]
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import gi

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst

import config

Gst.init(None)

RUN_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6

# The pipeline the sender actually builds.
#
# This used to insert an I420 -> BGR -> I420 round trip of its own, which the
# sender no longer has.  Diagnosing PTS behaviour on a pipeline that differs
# from the one being measured is worse than not diagnosing it: the conversion
# costs roughly 8 ms and changes how buffers are timed, so the numbers looked
# plausible and described the wrong thing.
PIPELINE = f"""
    {config.video_source_description()}
    !
    identity name=timestamp_probe
    !
    x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max=30
    !
    h264parse name=parse
    !
    rtph264pay name=pay config-interval=1 pt=96
    !
    application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000
    !
    queue name=rtp_queue
    !
    fakesink name=sink sync=false
"""

TARGETS = [
    ("identity.src", "timestamp_probe", "src"),
    ("x264enc.src", "enc", "src"),
    ("h264parse.src", "parse", "src"),
    ("rtph264pay.src", "pay", "src"),
    ("queue.sink", "rtp_queue", "sink"),
    ("queue.src", "rtp_queue", "src"),
]

counts = {}


def make_probe(label):
    def probe(pad, info):
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK

        counts[label] = counts.get(label, 0) + 1
        n = counts[label]

        # First few buffers, then a sparse trace.
        if n <= 3 or n % 90 == 0:
            print(
                f"{label:18s} n={n:6d} "
                f"pts={buffer.pts} "
                f"dts={buffer.dts} "
                f"dur={buffer.duration} "
                f"size={buffer.get_size()}",
                flush=True,
            )

        return Gst.PadProbeReturn.OK

    return probe


pipeline = Gst.parse_launch(PIPELINE)

print("=== attaching probes ===", flush=True)
for label, element_name, pad_name in TARGETS:
    element = pipeline.get_by_name(element_name)
    if element is None:
        print(f"{label:18s} !! element {element_name!r} NOT FOUND", flush=True)
        continue

    pad = element.get_static_pad(pad_name)
    if pad is None:
        print(f"{label:18s} !! pad {pad_name!r} NOT FOUND", flush=True)
        continue

    probe_id = pad.add_probe(Gst.PadProbeType.BUFFER, make_probe(label))
    print(f"{label:18s} probe attached (id={probe_id})", flush=True)

print(f"=== running for {RUN_SECONDS}s ===", flush=True)

pipeline.set_state(Gst.State.PLAYING)

loop = GLib.MainLoop()


def stop():
    pipeline.set_state(Gst.State.NULL)
    loop.quit()
    return False


GLib.timeout_add_seconds(RUN_SECONDS, stop)
loop.run()

print("=== buffer counts per pad ===", flush=True)
for label, _, _ in TARGETS:
    print(f"{label:18s} {counts.get(label, 0)}", flush=True)

print("DONE", flush=True)
