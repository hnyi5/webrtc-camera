import gi
import time
from datetime import datetime

gi.require_version("Gst", "1.0")
from gi.repository import Gst


Gst.init(None)


# ============================================================
# 全局状态
# ============================================================

frame_id = 0

wall_ns = 0
mono_ns = 0


# ============================================================
# 每帧 Probe
# ============================================================

def on_buffer(pad, info):

    global frame_id
    global wall_ns
    global mono_ns

    buffer = info.get_buffer()

    if buffer is None:
        return Gst.PadProbeReturn.OK

    # 当前帧编号
    frame_id += 1

    # 当前机器绝对时间
    wall_ns = time.time_ns()

    # 当前机器单调时钟
    mono_ns = time.monotonic_ns()

    # --------------------------------------------------------
    # 生成显示文字
    # --------------------------------------------------------

    wall_time = datetime.fromtimestamp(
        wall_ns / 1_000_000_000
    )

    milliseconds = (
        wall_ns % 1_000_000_000
    ) // 1_000_000

    text = (
        f"Frame: {frame_id:08d}\n"
        f"{wall_time.strftime('%Y-%m-%d %H:%M:%S')}"
        f".{milliseconds:03d}"
    )

    # --------------------------------------------------------
    # 获取 textoverlay
    # --------------------------------------------------------

    overlay = pipeline.get_by_name(
        "timestamp_overlay"
    )

    if overlay is not None:

        overlay.set_property(
            "text",
            text
        )

    # --------------------------------------------------------
    # 终端输出
    # --------------------------------------------------------

    print(
        f"Frame={frame_id:08d} "
        f"Wall={wall_time.strftime('%Y-%m-%d %H:%M:%S')}"
        f".{milliseconds:03d} "
        f"wall_ns={wall_ns} "
        f"mono_ns={mono_ns}"
    )

    return Gst.PadProbeReturn.OK


# ============================================================
# Pipeline
# ============================================================

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


pipeline = Gst.parse_launch(
    pipeline_description
)


# ============================================================
# 获取 timestamp probe
# ============================================================

timestamp_probe = pipeline.get_by_name(
    "timestamp_probe"
)

if timestamp_probe is None:

    print(
        "ERROR: timestamp_probe not found"
    )

    raise SystemExit(1)


# ============================================================
# 获取 probe pad
# ============================================================

src_pad = timestamp_probe.get_static_pad(
    "src"
)

if src_pad is None:

    print(
        "ERROR: timestamp_probe src pad not found"
    )

    raise SystemExit(1)


# ============================================================
# 安装 BUFFER probe
# ============================================================

src_pad.add_probe(
    Gst.PadProbeType.BUFFER,
    on_buffer
)


# ============================================================
# 启动 Pipeline
# ============================================================

print(
    "Starting latency timestamp test..."
)

print()

print(
    "Pipeline:"
)

print(
    "v4l2src"
    " -> JPEG"
    " -> jpegdec"
    " -> videoconvert"
    " -> timestamp_probe"
    " -> textoverlay"
    " -> autovideosink"
)

print()

print(
    "Close the video window to stop."
)

print()


pipeline.set_state(
    Gst.State.PLAYING
)


# ============================================================
# Main loop
# ============================================================

try:

    bus = pipeline.get_bus()

    while True:

        message = bus.timed_pop_filtered(
            Gst.CLOCK_TIME_NONE,
            Gst.MessageType.ERROR
            | Gst.MessageType.EOS
        )

        if message is None:
            continue

        if message.type == Gst.MessageType.ERROR:

            error, debug = message.parse_error()

            print(
                "ERROR:",
                error
            )

            print(
                "DEBUG:",
                debug
            )

            break

        if message.type == Gst.MessageType.EOS:

            print(
                "End of stream"
            )

            break

finally:

    pipeline.set_state(
        Gst.State.NULL
    )