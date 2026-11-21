"""Aggregate the bot's stderr SUMMARY lines (E002 telemetry) across matches.

    python scripts/summarize_stderr.py <file-or-dir> [...]

Reads every file given (directories recursively), takes the last `SUMMARY {...}` line per file, and
prints placement points (16 - place), placement distribution, kills, damage taken by source, zone
time, death-window attacker counts, latency, and fallbacks. Files without a SUMMARY are listed.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from collections import Counter


def files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in sorted(names):
                    yield os.path.join(root, n)
        else:
            yield p


def last_summary(path):
    s = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            i = line.find("SUMMARY {")
            if i >= 0:
                try:
                    s = json.loads(line[i + len("SUMMARY "):])
                except ValueError:
                    pass
    return s


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    rows, missing = [], []
    for path in files(argv):
        s = last_summary(path)
        (rows if s else missing).append((path, s))
    print(f"matches with SUMMARY: {len(rows)}; without: {len(missing)}")
    for p, _ in missing:
        print(f"  no SUMMARY: {p}")
    if not rows:
        return 1
    places = [s["end"]["place"] for _, s in rows if (s.get("end") or {}).get("place") is not None]
    if places:
        pts = [16 - p for p in places]
        sd = statistics.stdev(pts) if len(pts) > 1 else 0.0
        print(f"placement points: mean {statistics.mean(pts):.2f}  sd {sd:.2f}  se {sd / max(len(pts), 1) ** 0.5:.2f}  n {len(pts)}")
        print("place distribution: " + ", ".join(f"{k}:{v}" for k, v in sorted(Counter(places).items())))
    kills = [(s.get("end") or {}).get("kills", len(s.get("kills", []))) for _, s in rows]
    print(f"kills: mean {statistics.mean(kills):.2f}  total {sum(kills)}")
    t_last = [s["last"].get("t", 0) for _, s in rows if s.get("last")]
    if t_last:
        print(f"last observation t (s): median {statistics.median(t_last):.0f}  min {min(t_last):.0f}  max {max(t_last):.0f}")
    taken = Counter()
    for _, s in rows:
        taken.update(s.get("taken", {}))
    print("damage taken by source (total): " + ", ".join(f"{k} {v:.0f}" for k, v in taken.most_common()))
    dw = Counter()
    for _, s in rows:
        dw.update((s.get("death_window") or {}).get("damage", {}))
    print("death-window damage by source: " + ", ".join(f"{k} {v:.0f}" for k, v in dw.most_common()))
    att = Counter((s.get("death_window") or {}).get("attackers", 0) for _, s in rows)
    print("death-window distinct attackers: " + ", ".join(f"{k}:{v}" for k, v in sorted(att.items())))
    out = [s.get("out_zone_s", 0) for _, s in rows]
    print(f"time outside zone (s): mean {statistics.mean(out):.1f}  matches > 0: {sum(1 for o in out if o > 0)}")
    lat = [s.get("lat_ms", {}) for _, s in rows]
    print(f"latency: worst p99 {max(l.get('p99', 0) for l in lat)} ms  worst max {max(l.get('max', 0) for l in lat)} ms  "
          f"ticks >= 40 ms {sum(l.get('ge40', 0) for l in lat)}")
    print(f"fallbacks (act exceptions): {sum(s.get('fallbacks', 0) for _, s in rows)}  "
          f"observation gaps: {sum(s.get('gaps', 0) for _, s in rows)}")
    modes = Counter()
    for _, s in rows:
        modes.update(s.get("modes", {}))
    tot = sum(modes.values()) or 1
    print("mode share: " + ", ".join(f"{k} {100 * v / tot:.0f}%" for k, v in modes.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
