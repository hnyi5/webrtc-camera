import gi
import time
from datetime import datetime

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)


frame_id = 0

last_wall_ns = None
intervals_ms = []


pipeline_description = """
v4l2src device=/dev/video0 io-mode=mmap
! image/jpeg,width=1920,height=1080,framerate=30/1
! jpegdec
! videoconvert
! identity name=timestamp_probe
! textoverlay name=timestamp_overlay
    font-desc="Sans 32"
    halignment=left
    valignment=top
    xpad=30
    ypad=30
    shaded-background=true
! autovideosink
"""


pipeline = Gst.parse_launch(pipeline_description)

probe = pipeline.get_by_name("timestamp_probe")
overlay = pipeline.get_by_name("timestamp_overlay")


def on_buffer(pad, info):
    global frame_id
    global last_wall_ns

    buffer = info.get_buffer()

    if buffer is None:
        return Gst.PadProbeReturn.OK

    frame_id += 1

    # 当前系统绝对时间
    wall_ns = time.time_ns()

    # 当前单调时钟
    mono_ns = time.monotonic_ns()

    # 计算相邻帧间隔
    if last_wall_ns is not None:
        interval_ms = (wall_ns - last_wall_ns) / 1_000_000
        intervals_ms.append(interval_ms)

    last_wall_ns = wall_ns

    # 格式化时间
    dt = datetime.fromtimestamp(wall_ns / 1_000_000_000)

    milliseconds = (wall_ns % 1_000_000_000) // 1_000_000

    time_string = (
        f"{dt.strftime('%Y-%m-%d %H:%M:%S')}.{milliseconds:03d}"
    )

    text = (
        f"Frame: {frame_id:08d}\n"
        f"{time_string}"
    )

    overlay.set_property("text", text)

    # 每 30 帧打印一次，避免终端刷屏
    if frame_id % 30 == 0:

        current_fps = 0.0

        if len(intervals_ms) >= 10:
            recent = intervals_ms[-30:]

            avg_interval = sum(recent) / len(recent)

            if avg_interval > 0:
                current_fps = 1000.0 / avg_interval

        print(
            f"[SOURCE] "
            f"Frame={frame_id:08d} "
            f"Time={time_string} "
            f"mono_ns={mono_ns} "
            f"FPS={current_fps:.2f}"
        )

    return Gst.PadProbeReturn.OK


probe.get_static_pad("src").add_probe(
    Gst.PadProbeType.BUFFER,
    on_buffer
)


bus = pipeline.get_bus()

pipeline.set_state(Gst.State.PLAYING)

print("JPEG source latency test started.")
print("1920x1080 MJPEG @ 30 FPS")
print("Close the video window to stop.")


try:

    while True:

        message = bus.timed_pop_filtered(
            100 * Gst.MSECOND,
            Gst.MessageType.ERROR
            | Gst.MessageType.EOS
        )

        if message is None:
            continue

        if message.type == Gst.MessageType.ERROR:

            error, debug = message.parse_error()

            print("ERROR:", error)

            if debug:
                print("DEBUG:", debug)

            break

        elif message.type == Gst.MessageType.EOS:

            print("EOS")
            break


except KeyboardInterrupt:

    print("Stopped by user.")


finally:

    pipeline.set_state(Gst.State.NULL)

    print()
    print("========== SOURCE TEST RESULT ==========")

    if len(intervals_ms) > 0:

        avg = sum(intervals_ms) / len(intervals_ms)
        minimum = min(intervals_ms)
        maximum = max(intervals_ms)

        fps = 1000.0 / avg if avg > 0 else 0

        print(f"Frames captured : {frame_id}")
        print(f"Average interval: {avg:.3f} ms")
        print(f"Min interval    : {minimum:.3f} ms")
        print(f"Max interval    : {maximum:.3f} ms")
        print(f"Average FPS     : {fps:.2f}")

    else:

        print("No frame interval data.")
