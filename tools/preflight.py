#!/usr/bin/env python3
"""
Preflight check for porting this project to another Ubuntu 22.04 machine.

Run this FIRST on the target machine.  It reports, item by item, what is
present, what is missing, and how to fix it -- so a failed first run of the
sender is never the thing that tells you a dependency is missing.

Everything is checked defensively: a missing Python module or a missing
command is reported, not raised.

    python3 tools/preflight.py
"""

import importlib
import os
import re
import shutil
import socket
import subprocess
import sys

OK = "  [ok]  "
BAD = "  [!!]  "
WARN = "  [~]   "

failures = []
warnings = []


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def ok(message):
    print(OK + message)


def bad(message, fix=None):
    print(BAD + message)
    if fix:
        print("         -> " + fix)
    failures.append(message)


def warn(message, fix=None):
    print(WARN + message)
    if fix:
        print("         -> " + fix)
    warnings.append(message)


def run(command):
    """Run a command, returning (returncode, combined output)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        return result.returncode, result.stdout.decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - diagnostic script
        return 1, str(error)


# ---------------------------------------------------------------------------
section("1. operating system and python")
# ---------------------------------------------------------------------------

try:
    with open("/etc/os-release", encoding="utf-8") as handle:
        release = handle.read()
    pretty = re.search(r'PRETTY_NAME="([^"]+)"', release)
    print("         os      : " + (pretty.group(1) if pretty else "unknown"))
    if "22.04" not in release:
        warn(
            "this is not Ubuntu 22.04",
            "GStreamer 1.20.3 is what the pipeline was verified against",
        )
except FileNotFoundError:
    warn("cannot read /etc/os-release (not a Linux host?)")

print("         python  : " + sys.version.split()[0])
if hasattr(os, "uname"):
    print("         arch    : " + os.uname().machine)
print("         cpus    : " + str(os.cpu_count()))

if sys.version_info < (3, 8):
    bad("python3 is older than 3.8", "install python3 (3.10 ships with 22.04)")
else:
    ok("python3 is new enough")

# ---------------------------------------------------------------------------
section("2. python modules")
# ---------------------------------------------------------------------------

module_fixes = {
    "gi": "sudo apt install -y python3-gi",
    "websocket": "pip3 install --user websocket-client",
    "websockets": "pip3 install --user websockets",
}

for name in ("gi", "websocket", "websockets"):
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        ok(f"{name} {version}".strip())
    except ImportError:
        bad(f"python module {name!r} is missing", module_fixes[name])

# GStreamer introspection namespaces
try:
    import gi

    for namespace, version, fix in (
        (
            "Gst",
            "1.0",
            "sudo apt install -y python3-gst-1.0",
        ),
        (
            "GstWebRTC",
            "1.0",
            "sudo apt install -y gir1.2-gst-plugins-bad-1.0",
        ),
        (
            "GstSdp",
            "1.0",
            "sudo apt install -y gir1.2-gst-plugins-bad-1.0",
        ),
    ):
        try:
            gi.require_version(namespace, version)
            __import__("gi.repository", fromlist=[namespace])
            ok(f"gi namespace {namespace}-{version}")
        except Exception as error:  # noqa: BLE001
            bad(f"gi namespace {namespace} not usable: {error}", fix)
except ImportError:
    print("         (skipping gi namespace checks)")

# ---------------------------------------------------------------------------
section("3. gstreamer")
# ---------------------------------------------------------------------------

gst_launch = shutil.which("gst-launch-1.0")
gst_inspect = shutil.which("gst-inspect-1.0")

if not gst_launch:
    bad("gst-launch-1.0 not found", "sudo apt install -y gstreamer1.0-tools")
else:
    _, output = run("gst-launch-1.0 --version")
    print("         " + output.strip().splitlines()[0])
    ok("gst-launch-1.0 present")

ELEMENTS = {
    "v4l2src": "gstreamer1.0-plugins-good",
    "jpegdec": "gstreamer1.0-plugins-good",
    "videoconvert": "gstreamer1.0-plugins-base",
    "queue": "gstreamer1.0-plugins-base",
    "identity": "gstreamer1.0-plugins-base",
    "h264parse": "gstreamer1.0-plugins-bad",
    "rtph264pay": "gstreamer1.0-plugins-good",
    "x264enc": "gstreamer1.0-plugins-ugly",
    "webrtcbin": "gstreamer1.0-plugins-bad",
    "fakesink": "gstreamer1.0-plugins-base",
}

if gst_inspect:
    missing = []
    for element, package in ELEMENTS.items():
        code, _ = run(f"gst-inspect-1.0 {element} >/dev/null 2>&1")
        if code != 0:
            missing.append((element, package))

    if not missing:
        ok(f"all {len(ELEMENTS)} required GStreamer elements present")
    else:
        for element, package in missing:
            bad(
                f"GStreamer element {element!r} missing",
                f"sudo apt install -y {package}",
            )
else:
    bad("gst-inspect-1.0 not found", "sudo apt install -y gstreamer1.0-tools")

# ---------------------------------------------------------------------------
section("4. video devices")
# ---------------------------------------------------------------------------

devices = sorted(
    "/dev/" + name
    for name in os.listdir("/dev")
    if name.startswith("video")
)

if not devices:
    bad(
        "no /dev/video* device found",
        "is the camera connected and passed through? (in a VM: "
        "VM > Removable Devices > connect it)",
    )
else:
    ok("devices: " + " ".join(devices))

    for device in devices:
        code, output = run(f"v4l2-ctl -d {device} --list-formats-ext 2>&1")
        if code != 0:
            warn(f"{device}: cannot query ({output.strip().splitlines()[:1]})")
            continue

        # Find which formats offer 1920x1080 at 30 fps.
        capable = []
        current_format = None
        current_size = None

        for line in output.splitlines():
            format_match = re.search(r"\[\d+\]:\s*'([^']+)'", line)
            if format_match:
                current_format = format_match.group(1)
                current_size = None
                continue

            size_match = re.search(r"Size:\s*Discrete\s+(\d+x\d+)", line)
            if size_match:
                current_size = size_match.group(1)
                continue

            interval_match = re.search(r"\((\d+\.\d+)\s*fps\)", line)
            if interval_match and current_format and current_size == "1920x1080":
                if float(interval_match.group(1)) >= 29.0:
                    capable.append(current_format)

        if capable:
            ok(f"{device}: 1920x1080@30 available as {sorted(set(capable))}")
        else:
            warn(
                f"{device}: no 1920x1080@30 in the listed formats",
                "run: v4l2-ctl -d %s --list-formats-ext   and adjust the caps "
                "in the sender if needed" % device,
            )

if not os.access("/dev/video0", os.R_OK):
    warn(
        "current user cannot read /dev/video0",
        "sudo usermod -aG video $USER   then log out and back in",
    )
else:
    ok("current user can read /dev/video0")

# ---------------------------------------------------------------------------
section("5. ports and services")
# ---------------------------------------------------------------------------


def port_in_use(port):
    probe = socket.socket()
    try:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


for port, name in ((8765, "signaling"), (8000, "http page")):
    if port_in_use(port):
        warn(f"port {port} ({name}) is already in use")
    else:
        ok(f"port {port} ({name}) is free")

# ---------------------------------------------------------------------------
section("6. optional tooling (node)")
# ---------------------------------------------------------------------------

node = shutil.which("node")
if node:
    _, output = run("node --version")
    version = output.strip()
    major = 0
    match = re.match(r"v(\d+)", version)
    if match:
        major = int(match.group(1))
    if major >= 22:
        ok(f"node {version} (tools/*.mjs will work)")
    else:
        warn(
            f"node {version} is older than 22",
            "the tools use the built-in WebSocket and fetch",
        )
else:
    warn("node not found - only needed for the automated test tools")

# ---------------------------------------------------------------------------
section("summary")
# ---------------------------------------------------------------------------

print(f"         failures : {len(failures)}")
print(f"         warnings : {len(warnings)}")

if failures:
    print()
    print("Blocking problems to fix before running the sender:")
    for item in failures:
        print("  - " + item)
    print()
    print("After fixing everything, run this check again.")
    sys.exit(1)

print()
print("No blocking problems found.  Next steps:")
print("  1. confirm the caps in webrtc_sender_Now_latency_probe.py match this camera")
print("  2. gst-launch-1.0 -v v4l2src device=/dev/video0 io-mode=mmap num-buffers=30")
print("       ! image/jpeg,width=1920,height=1080,framerate=30/1 ! jpegdec ! fakesink")
print("  3. see docs/PORTING.md section 3 for the full startup order")
sys.exit(0)
