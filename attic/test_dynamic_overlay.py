#!/usr/bin/env python3

import time
from datetime import datetime

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")

from gi.repository import Gst, GLib

Gst.init(None)


pipeline_description = """
    v4l2src device=/dev/video0 io-mode=mmap
    !
    image/jpeg,width=1920,height=1080,framerate=30/1
    !
    jpegdec
    !
    videoconvert
    !
    textoverlay name=overlay
        font-desc="Sans 32"
        halignment=left
        valignment=top
        xpad=30
        ypad=30
        shaded-background=true
    !
    autovideosink
"""

pipeline = Gst.parse_launch(pipeline_description)

overlay = pipeline.get_by_name("overlay")

if overlay is None:
    raise RuntimeError("Cannot find textoverlay")


counter = 0


def update_overlay():
    global counter

    counter += 1

    now = datetime.now()

    timestamp = now.strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )[:-3]

    text = (
        f"Frame: {counter:08d}\n"
        f"{timestamp}"
    )

    overlay.set_property("text", text)

    print("[OVERLAY]", text.replace("\n", " | "))

    return True


bus = pipeline.get_bus()
bus.add_signal_watch()


def on_message(bus, message):

    if message.type == Gst.MessageType.ERROR:

        error, debug = message.parse_error()

        print("[GSTREAMER ERROR]")
        print(error)

        if debug:
            print(debug)

        loop.quit()

    elif message.type == Gst.MessageType.EOS:

        print("[GSTREAMER] EOS")

        loop.quit()


bus.connect("message", on_message)


loop = GLib.MainLoop()


# 每 100 ms 更新一次文字
GLib.timeout_add(
    100,
    update_overlay
)


print("[TEST] Starting pipeline...")

pipeline.set_state(
    Gst.State.PLAYING
)

try:
    loop.run()

except KeyboardInterrupt:
    print("\n[TEST] Stopping...")

finally:
    pipeline.set_state(
        Gst.State.NULL
    )
