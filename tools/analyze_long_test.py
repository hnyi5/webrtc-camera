#!/usr/bin/env python3
"""
Analyse the JSONL produced by long_test.mjs.

Reports the latency distribution, frame rate, match/miss totals, clock-offset
behaviour, and any gaps (which would mean a lost session).

Usage: python analyze_long_test.py <file.jsonl>
"""

import json
import statistics
import sys
from datetime import datetime

PATH = sys.argv[1] if len(sys.argv) > 1 else "long-test.jsonl"


def number(value):
    """Panel values arrive as text like '20.8 ms' or '--'."""
    if not isinstance(value, str):
        return None

    text = value.strip()

    for suffix in ("ms", "Mbps", "kbps", "bps", "fps", "FPS", "%"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()

    text = text.replace(",", "")

    try:
        return float(text)
    except ValueError:
        return None


def percentile(values, fraction):
    if not values:
        return None

    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)

    if lower == upper:
        return ordered[lower]

    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


records = []

with open(PATH, encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

samples = [r for r in records if "latency" in r]
exceptions = [r for r in records if r.get("kind") == "exception"]
errors = [r for r in records if r.get("sampleError")]

if not samples:
    print("no samples found")
    sys.exit(1)

first = samples[0]
last = samples[-1]
duration_s = (last["t"] - first["t"]) / 1000

print("=" * 72)
print(f"file            : {PATH}")
print(f"window          : {datetime.fromtimestamp(first['t'] / 1000)}")
print(f"                  {datetime.fromtimestamp(last['t'] / 1000)}")
print(f"duration        : {duration_s / 60:.1f} min")
print(f"samples         : {len(samples)}  (read errors {len(errors)}, "
      f"browser exceptions {len(exceptions)})")
print("=" * 72)

metrics = [
    ("latency (source->callback)", "latency"),
    ("source->receive", "captureToReceive"),
    ("receive->callback", "receiveToCallback"),
    ("source->expected display", "captureToDisplay"),
    ("decoded frames/s (stream)", "decodedFps"),
    ("rvfc frames/s (presented)", "fps"),
]

print(f"{'metric':30s} {'n':>5s} {'min':>8s} {'P50':>8s} {'P95':>8s} "
      f"{'P99':>8s} {'max':>8s} {'mean':>8s}")

for label, key in metrics:
    values = [v for v in (number(r.get(key)) for r in samples) if v is not None]
    if not values:
        continue
    print(
        f"{label:30s} {len(values):5d} "
        f"{min(values):8.1f} {percentile(values, 0.50):8.1f} "
        f"{percentile(values, 0.95):8.1f} {percentile(values, 0.99):8.1f} "
        f"{max(values):8.1f} {statistics.mean(values):8.1f}"
    )

print()

fps = [v for v in (number(r.get("fps")) for r in samples) if v is not None]
bitrate = [v for v in (number(r.get("bitrate")) for r in samples) if v is not None]

if fps:
    print(f"fps      : min {min(fps):.1f}  P50 {percentile(fps, 0.5):.1f}  "
          f"P95 {percentile(fps, 0.95):.1f}  max {max(fps):.1f}")

if bitrate:
    print(f"bitrate  : min {min(bitrate):.2f}  P50 {percentile(bitrate, 0.5):.2f}  "
          f"max {max(bitrate):.2f} Mbps")

print()

offsets = [v for v in (number(r.get("clockOffset")) for r in samples) if v is not None]
if offsets:
    drift = offsets[-1] - offsets[0]
    span_min = (last["t"] - first["t"]) / 60000
    print(f"clock offset: first {offsets[0]:.1f} ms  last {offsets[-1]:.1f} ms  "
          f"drift {drift:+.1f} ms over {span_min:.0f} min "
          f"({drift / max(span_min, 1) * 60:+.1f} ms/h)")
    print(f"              min {min(offsets):.1f}  max {max(offsets):.1f}")

print()


def match_miss(record):
    value = record.get("matchMiss") or ""
    parts = [p.strip() for p in value.split("/")]

    if len(parts) != 2:
        return None, None

    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


# Sum the positive deltas instead of last - first.  A page reload resets the
# counters to zero, and last - first then reports a negative total -- which is
# exactly what a recovery in the middle of a run used to produce.
added_match = 0
added_miss = 0
previous_pair = None

for record in samples:
    pair = match_miss(record)

    if pair[0] is None or pair[1] is None:
        previous_pair = None
        continue

    if previous_pair is not None:
        delta_match = pair[0] - previous_pair[0]
        delta_miss = pair[1] - previous_pair[1]

        # A negative delta means the page reloaded and the counter restarted.
        if delta_match >= 0 and delta_miss >= 0:
            added_match += delta_match
            added_miss += delta_miss

    previous_pair = pair

if added_match or added_miss:
    total = added_match + added_miss
    rate = 100.0 * added_match / total if total else 0.0
    print(f"match/miss during the run: {added_match} / {added_miss}  "
          f"-> {rate:.2f}% matched")

print()

states = {}
for record in samples:
    key = (record.get("conn"), record.get("peer"), record.get("ice"))
    states[key] = states.get(key, 0) + 1

print("connection states seen:")
for key, count in sorted(states.items(), key=lambda kv: -kv[1]):
    print(f"  {count:5d} x  conn={key[0]} peer={key[1]} ice={key[2]}")

print()

paths = {}
for record in samples:
    value = record.get("icePath")
    if value:
        paths[value] = paths.get(value, 0) + 1

print("ICE path selected:")
if not paths:
    print("  (not recorded -- page build predates the ICE path row)")
else:
    for value, count in sorted(paths.items(), key=lambda kv: -kv[1]):
        if "TURN" in value:
            note = "   <-- RELAYED: not comparable with a direct path"
        elif "P2P" in value:
            note = "   (direct peer-to-peer)"
        elif "LAN" in value:
            note = "   (same LAN)"
        else:
            note = ""
        print(f"  {count:5d} x  {value}{note}")

print()

gaps = []
for previous, current in zip(samples, samples[1:]):
    delta = current["elapsedS"] - previous["elapsedS"]
    if delta > 30:
        gaps.append((previous["elapsedS"], current["elapsedS"], delta))

print(f"gaps longer than 30 s: {len(gaps)}")
for start_s, end_s, delta in gaps:
    print(f"  {start_s}s -> {end_s}s  ({delta}s missing)")

if exceptions:
    print()
    print("browser exceptions:")
    for record in exceptions[:10]:
        print("  ", str(record.get("detail"))[:160])
