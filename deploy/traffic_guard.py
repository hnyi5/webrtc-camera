#!/usr/bin/env python3
"""
Hard cap on outbound public traffic.

WHY THIS EXISTS
---------------
Pay-by-traffic has no spending ceiling, and a cost alarm only tells you that
money is being spent -- it does not stop it.  A TURN relay that gets abused can
push hundreds of gigabytes a day; on Aliyun's CNY 0.80/GB that is hundreds of
yuan, and the first you hear about it is an alarm that arrives after some of it
is already gone.

So this watches the outbound counter and, past a daily budget, STOPS coturn.
Losing the relay is recoverable -- P2P keeps working, and coturn restarts in a
second.  A runaway bill is not.

WHAT IT MEASURES
----------------
The interface counter, not coturn's own accounting, so signalling, ssh and
package downloads are included.  Those are kilobytes to megabytes; the relay is
the only thing here that can move gigabytes.  The budget is set far above
normal use, so this is a runaway detector rather than a meter.

The counter is read from the interface that carries the default route, which is
the one whose outbound bytes the provider bills.

USAGE
-----
    sudo python3 deploy/traffic_guard.py --status
    sudo python3 deploy/traffic_guard.py --once --budget-gb 5
    sudo python3 deploy/traffic_guard.py --budget-gb 5          # forever

Installed as a systemd service by deploy/setup-server.sh.  State lives in
/var/lib/dsh-camera/traffic.json so it survives restarts and resets at local
midnight.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import date

STATE_DIR = pathlib.Path(os.environ.get("DSH_GUARD_STATE", "/var/lib/dsh-camera"))
STATE_FILE = STATE_DIR / "traffic.json"

DEFAULT_BUDGET_GB = 5.0
DEFAULT_INTERVAL = 30.0

MB = 1000 * 1000


def log(message):
    print("[GUARD] " + message, flush=True)


def budget_from_env(name, default):
    """Read the daily budget, falling back instead of dying.

    float() on a malformed value raises while argparse is still being built, so
    the process exits before the first measurement -- and systemd restarts it
    every ten seconds, showing "activating" forever, while nothing is actually
    guarded.  A safety device must not fail this way, and it is inconsistent
    with config.py, which already degrades gracefully for the integer settings.
    """
    raw = os.environ.get(name)

    if raw is None or raw == "":
        return float(default)

    try:
        return float(raw)
    except (TypeError, ValueError):
        log("{0}={1!r} is not a number; falling back to {2} GB".format(
            name, raw, default))
        return float(default)


def default_interface():
    """Name of the interface carrying the IPv4 default route.

    `ip route` is tried first because its output is unambiguous.  /proc/net/route
    is the fallback for systems without iproute2, and in that file the default
    route is the row whose Destination AND Mask are BOTH 00000000 -- getting
    that comparison the wrong way round matches only the specific routes, so
    nothing is ever found.
    """
    try:
        result = subprocess.run(
            ["ip", "-4", "route", "show", "default"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )

        for line in (result.stdout or "").splitlines():
            fields = line.split()
            if "dev" in fields:
                return fields[fields.index("dev") + 1]
    except Exception:  # noqa: BLE001
        pass

    path = "/proc/net/route"

    if not os.path.exists(path):
        return None

    with open(path, encoding="ascii") as handle:
        lines = handle.read().splitlines()

    for line in lines[1:]:
        fields = line.split()

        if (
            len(fields) >= 8
            and fields[1] == "00000000"
            and fields[7] == "00000000"
        ):
            return fields[0]

    return None


def read_counters(interface):
    """(rx_bytes, tx_bytes) for one interface."""
    with open("/proc/net/dev", encoding="ascii") as handle:
        for line in handle:
            if ":" not in line:
                continue
            name, _, rest = line.partition(":")
            if name.strip() != interface:
                continue
            fields = rest.split()
            # rx bytes is field 0, tx bytes is field 8
            return int(fields[0]), int(fields[8])

    raise RuntimeError("interface {0} not in /proc/net/dev".format(interface))


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(STATE_FILE)


def trip(reason, used_bytes, budget_bytes):
    """Stop the relay and make it obvious in the logs."""
    log("=" * 70)
    log("DAILY BUDGET EXCEEDED -- stopping the relay")
    log("  {0}".format(reason))
    log("  used   : {0:.2f} GB".format(used_bytes / 1e9))
    log("  budget : {0:.2f} GB".format(budget_bytes / 1e9))
    log("=" * 70)

    result = subprocess.run(
        ["systemctl", "stop", "coturn"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode == 0:
        log("coturn stopped.  P2P still works; only relaying is off.")
    else:
        log("could not stop coturn (rc={0}): {1}".format(
            result.returncode, (result.stdout or "").strip()))
        log("stop it by hand:  sudo systemctl stop coturn")

    log("before restarting it, look at what happened:")
    log("    tail -50 /var/log/turnserver.log")
    log("    grep rejected $(systemctl show -p FragmentPath dsh-signaling | cut -d= -f2)")
    log("then:  sudo systemctl start coturn")


def check(budget_bytes, state, interface_override=None):
    """One measurement.  Returns the updated state."""
    interface = (
        interface_override
        or state.get("interface")
        or default_interface()
    )

    if interface is None:
        log("no default route found; cannot measure traffic")
        return state

    rx, tx = read_counters(interface)
    today = date.today().isoformat()

    if state.get("date") != today or "start_tx" not in state:
        log("new day ({0}); starting the counter at tx={1}".format(today, tx))
        state = {
            "date": today,
            "interface": interface,
            "start_tx": tx,
            "start_rx": rx,
            "stopped": False,
        }

    used_tx = tx - state["start_tx"]
    used_rx = rx - state["start_rx"]

    state["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["used_tx"] = used_tx
    state["used_rx"] = used_rx
    state["budget_tx"] = budget_bytes

    # A counter going backwards means the interface was reset or renamed.
    if used_tx < 0:
        log("counter went backwards; re-anchoring")
        state["start_tx"] = tx
        state["start_rx"] = rx
        used_tx = 0

    if used_tx >= budget_bytes and not state.get("stopped"):
        trip(
            "outbound traffic on {0} passed the daily budget".format(interface),
            used_tx,
            budget_bytes,
        )
        state["stopped"] = True
        state["stopped_at"] = state["last_check"]

    return state


def show_status(state, budget_bytes):
    used = state.get("used_tx", 0)
    print("traffic guard status")
    print("=" * 60)
    print("  date       : {0}".format(state.get("date", "-")))
    print("  interface  : {0}".format(state.get("interface", "-")))
    print("  outbound   : {0:.3f} GB".format(used / 1e9))
    print("  inbound    : {0:.3f} GB".format(state.get("used_rx", 0) / 1e9))
    print("  budget     : {0:.3f} GB per day".format(budget_bytes / 1e9))
    print("  tripped    : {0}".format("yes, at " + state["stopped_at"]
                                      if state.get("stopped") else "no"))
    print("  last check : {0}".format(state.get("last_check", "-")))

    if budget_bytes:
        percent = 100.0 * used / budget_bytes
        print("  used       : {0:.1f}% of budget".format(percent))

    print()
    print("  cost so far today: CNY {0:.2f} at 0.80/GB".format(used / 1e9 * 0.80))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--budget-gb",
        type=float,
        default=budget_from_env("DSH_DAILY_TX_BUDGET_GB", DEFAULT_BUDGET_GB),
        help="outbound budget per day, in GB (default %(default)s)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="seconds between checks (default %(default)s)",
    )
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument(
        "--interface",
        default=os.environ.get("DSH_GUARD_INTERFACE") or None,
        help="measure this interface instead of detecting the default route",
    )
    parser.add_argument("--status", action="store_true", help="print state and exit")
    arguments = parser.parse_args()

    budget_bytes = arguments.budget_gb * 1e9
    state = load_state()

    if arguments.status:
        show_status(state, budget_bytes)
        return 0

    if arguments.once:
        state = check(budget_bytes, state, arguments.interface)
        save_state(state)
        show_status(state, budget_bytes)
        return 0

    log("watching outbound traffic, budget {0:.2f} GB/day, every {1:.0f}s".format(
        arguments.budget_gb, arguments.interval))
    log("state file: {0}".format(STATE_FILE))

    while True:
        try:
            state = check(budget_bytes, state, arguments.interface)
            save_state(state)
        except Exception as error:  # noqa: BLE001
            # Never die from a monitoring error: a guard that stops watching is
            # worse than no guard, because it looks like protection.
            log("check failed: {0}: {1}".format(type(error).__name__, error))

        time.sleep(arguments.interval)


if __name__ == "__main__":
    sys.exit(main())
