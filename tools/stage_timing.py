#!/usr/bin/env python3
"""
Per-stage timing for the camera pipeline.

Answers "where does the camera-side latency actually go" with measurements made
entirely inside the virtual machine, so no cross-machine clock problem can
contaminate them.

Two things are measured:

1. The V4L2 queue wait (step 4 in the technical notes' chain).

   With do-timestamp=false, v4l2src passes the driver's buffer timestamp
   through as the buffer PTS, converted from the clock's epoch into running
   time.  uvcvideo timestamps a buffer when the frame's last USB packet has
   arrived, so

       running_now - buffer.pts

   is how long that frame sat in the driver's queue before GStreamer took it.
   If do-timestamp is left at its default (true) the PTS is overwritten with
   the dequeue time and this measurement is always zero -- which is exactly why
   the flag is printed alongside the result.

2. The per-stage processing time.

   Every probe records time.monotonic_ns(), and the stages are compared
   buffer-by-buffer, so each delta is a pure local duration.

Modes (each uses fakesink, so nothing else competes for the CPU):

    light   v4l2src ! fakesink                       consumer is infinitely fast
    decode  v4l2src ! jpegdec ! fakesink             adds JPEG decode
    full    v4l2src ! jpegdec ! convert ! x264enc    the real chain

Comparing light with full is the direct test of whether a slow consumer is
letting the driver's queue build up.

Usage: python3 tools/stage_timing.py <mode> [seconds] [do_timestamp]
"""

import os
import statistics
import sys
import time

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

import gi

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst

import config

Gst.init(None)

MODE = sys.argv[1] if len(sys.argv) > 1 else "light"
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
DO_TIMESTAMP = (sys.argv[3] if len(sys.argv) > 3 else "false").lower() == "true"

CAPS = config.VIDEO_CAPS

v4l2 = (
    f"v4l2src name=cam device={config.VIDEO_DEVICE} io-mode=mmap "
    f"do-timestamp={'true' if DO_TIMESTAMP else 'false'} ! {CAPS}"
)

PIPELINES = {
    "light": f"{v4l2} ! fakesink name=sink sync=false",
    "decode": f"{v4l2} ! jpegdec name=jdec ! fakesink name=sink sync=false",
    "full": (
        f"{v4l2} ! jpegdec name=jdec ! videoconvert name=conv1"
        " ! video/x-raw,format=BGR ! identity name=ident"
        " ! videoconvert name=conv2 ! video/x-raw,format=I420"
        " ! x264enc name=enc tune=zerolatency speed-preset=ultrafast"
        "   bitrate=4000 key-int-max=30"
        " ! h264parse name=parse ! rtph264pay name=pay config-interval=1 pt=96"
        " ! queue name=q ! fakesink name=sink sync=false"
    ),
    # Same chain with the vestigial BGR round trip removed: jpegdec already
    # produces I420 and x264enc wants I420, so neither videoconvert is needed.
    "optimal": (
        f"{v4l2} ! jpegdec name=jdec ! identity name=ident"
        " ! x264enc name=enc tune=zerolatency speed-preset=ultrafast"
        "   bitrate=4000 key-int-max=30"
        " ! h264parse name=parse ! rtph264pay name=pay config-interval=1 pt=96"
        " ! queue name=q ! fakesink name=sink sync=false"
    ),
}

TARGETS = {
    "light": [("cam", "src", "v4l2src out")],
    "decode": [("cam", "src", "v4l2src out"), ("jdec", "src", "jpegdec out")],
    "full": [
        ("cam", "src", "v4l2src out"),
        ("jdec", "src", "jpegdec out"),
        ("conv1", "src", "conv->BGR out"),
        ("ident", "src", "identity out (probe point)"),
        ("conv2", "src", "conv->I420 out"),
        ("enc", "src", "x264enc out"),
        ("pay", "src", "rtph264pay out"),
        ("q", "sink", "queue in"),
    ],
    "optimal": [
        ("cam", "src", "v4l2src out"),
        ("jdec", "src", "jpegdec out"),
        ("ident", "src", "identity out (probe point)"),
        ("enc", "src", "x264enc out"),
        ("pay", "src", "rtph264pay out"),
        ("q", "sink", "queue in"),
    ],
}

if MODE not in PIPELINES:
    print(f"unknown mode {MODE!r}; expected one of {list(PIPELINES)}")
    sys.exit(2)

pipeline = Gst.parse_launch(PIPELINES[MODE])

records = {label: [] for _, _, label in TARGETS[MODE]}
first_pts = {"value": None}


def make_probe(label):
    def probe(pad, info):
        buffer = info.get_buffer()

        if buffer is None:
            return Gst.PadProbeReturn.OK

        clock = pipeline.get_clock()
        base = pipeline.get_base_time()

        running_now = None
        if clock is not None:
            running_now = clock.get_time() - base

        pts = buffer.pts
        if pts == Gst.CLOCK_TIME_NONE:
            pts = None

        wait = None
        if running_now is not None and pts is not None:
            wait = running_now - pts

        records[label].append(
            {
                "mono": time.monotonic_ns(),
                "pts": pts,
                "running_now": running_now,
                "wait": wait,
                "size": buffer.get_size(),
            }
        )

        if label == "v4l2src out" and first_pts["value"] is None:
            first_pts["value"] = pts

        return Gst.PadProbeReturn.OK

    return probe


print(f"mode            : {MODE}")
print(f"do-timestamp    : {DO_TIMESTAMP}")
print(f"duration        : {SECONDS}s")

for element_name, pad_name, label in TARGETS[MODE]:
    element = pipeline.get_by_name(element_name)
    if element is None:
        print(f"  !! element {element_name!r} not found")
        continue
    pad = element.get_static_pad(pad_name)
    if pad is None:
        print(f"  !! pad {element_name}.{pad_name} not found")
        continue
    pad.add_probe(Gst.PadProbeType.BUFFER, make_probe(label))
    print(f"  probe: {label}")

pipeline.set_state(Gst.State.PLAYING)

loop = GLib.MainLoop()


def stop():
    pipeline.set_state(Gst.State.NULL)
    loop.quit()
    return False


GLib.timeout_add_seconds(SECONDS, stop)
loop.run()


def ms(ns):
    return None if ns is None else ns / 1e6


print()
print("=" * 74)
print("buffer counts")
for _, _, label in TARGETS[MODE]:
    print(f"  {label:28s} {len(records[label])}")

print()
print("running_now - buffer.pts   *** NOT a driver queue wait ***")
print("  This is a formula check only.  Running it with do-timestamp=true")
print("  (where v4l2src overwrites the driver timestamp with the dequeue time)")
print("  gives the same value, so the offset comes from GStreamer's own")
print("  base-time/latency convention, not from the driver.  What IS meaningful")
print("  is how the tail grows under load: that is real queueing.")
for _, _, label in TARGETS[MODE]:
    waits = [r["wait"] for r in records[label] if r["wait"] is not None]
    if not waits:
        print(f"  {label:28s} no data")
        continue
    ordered = sorted(waits)
    print(
        f"  {label:28s} n={len(waits):4d}  "
        f"min={ms(ordered[0]):7.2f}  P50={ms(statistics.median(ordered)):7.2f}  "
        f"P95={ms(ordered[int(len(ordered) * 0.95)]):7.2f}  "
        f"max={ms(ordered[-1]):7.2f} ms"
    )

if MODE != "light":
    print()
    print("per-stage processing time")
    print("  Frames are matched by buffer PTS where possible (exact frame")
    print("  identity before x264enc); index matching is only used when PTS")
    print("  matching fails, and is flagged as such.")

    labels = [label for _, _, label in TARGETS[MODE]]

    def stage_deltas(earlier_records, later_records):
        by_pts = {}
        for record in later_records:
            if record["pts"] is not None:
                by_pts.setdefault(record["pts"], record)

        matched = []
        for record in earlier_records:
            if record["pts"] is None:
                continue
            partner = by_pts.get(record["pts"])
            if partner is not None:
                matched.append(partner["mono"] - record["mono"])

        if len(matched) >= 5:
            return matched, "pts"

        count = min(len(earlier_records), len(later_records))
        return (
            [
                later_records[i]["mono"] - earlier_records[i]["mono"]
                for i in range(count)
            ],
            "index",
        )

    for earlier, later in zip(labels, labels[1:]):
        deltas, how = stage_deltas(records[earlier], records[later])
        if len(deltas) < 5:
            print(f"  {earlier} -> {later}: not enough data")
            continue
        deltas = sorted(deltas)
        print(
            f"  {earlier:28s} -> {later:28s} "
            f"n={len(deltas):4d} [{how:5s}]  "
            f"P50={ms(statistics.median(deltas)):7.2f} ms  "
            f"max={ms(deltas[-1]):7.2f} ms"
        )

    deltas, how = stage_deltas(records[labels[0]], records[labels[-1]])
    if len(deltas) >= 5:
        deltas = sorted(deltas)
        print()
        print(
            f"  TOTAL {labels[0]} -> {labels[-1]} "
            f"n={len(deltas)} [{how}]  "
            f"P50={ms(statistics.median(deltas)):7.2f} ms  "
            f"max={ms(deltas[-1]):7.2f} ms"
        )

print()
print("first buffer PTS (running time at the first observed frame): "
      f"{ms(first_pts['value'])} ms")
print("DONE")
