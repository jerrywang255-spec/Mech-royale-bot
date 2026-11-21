"""Paired placement comparison of two variants from a real match runner (official or local).

    python scripts/compare_runs.py runs.csv [--ref base --cand cover]

runs.csv columns: match,variant,place,stderr
  match   : the pairing key; identical seed, spawn slot and opponents for both variants (e.g. "seed17-slot3")
  variant : e.g. base (SURVIVOR_COVER=0) or cover (SURVIVOR_COVER=1), same Docker image
  place   : finishing place 1..16 from the runner
  stderr  : path to that match's bot stderr (optional; enables the cover diagnostics)

Prints: matches, mean placement points (16 - place) per variant, paired difference with its standard
error, improved/tied/worsened counts, the worst regressions, and from stderr: cover episodes, how they
ended, HP on first reaching cover, and eliminations within 10 s of a cover start (LAST line = last
observation before exit, so it is only an elimination time when place > 1).
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
from collections import Counter


def parse_stderr(path):
    out = {"starts": [], "ends": Counter(), "reached_hp": [], "last_t": None, "errors": 0}
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if ln.startswith("COVER start"):
                out["starts"].append(float(ln.split(" t=")[1].split()[0]))
            elif ln.startswith("COVER end"):
                out["ends"][ln.split("reason=")[1].split()[0]] += 1
                v = ln.split("reached_hp=")[1].split()[0]
                if v != "None":
                    out["reached_hp"].append(float(v))
            elif ln.startswith("LAST "):
                out["last_t"] = float(ln.split(" t=")[1].split()[0])
            if "Traceback" in ln or "act() failed" in ln or "cover disabled" in ln:
                out["errors"] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--ref", default="base")
    ap.add_argument("--cand", default="cover")
    args = ap.parse_args()
    rows = {}
    with open(args.csv, newline="") as f:
        for r in csv.DictReader(f):
            rows[(r["variant"], r["match"])] = r
    keys = sorted({m for v, m in rows if v == args.ref} & {m for v, m in rows if v == args.cand})
    if not keys:
        print("no paired matches")
        return 1
    pts = {v: [16 - int(float(rows[(v, m)]["place"])) for m in keys] for v in (args.ref, args.cand)}
    d = [c - b for b, c in zip(pts[args.ref], pts[args.cand])]
    se = statistics.stdev(d) / len(d) ** 0.5 if len(d) > 1 else float("nan")
    print(f"paired matches: {len(keys)}")
    for v in (args.ref, args.cand):
        print(f"  {v:8s} mean placement points {statistics.mean(pts[v]):.2f} "
              f"(place dist {dict(sorted(Counter(16 - p for p in pts[v]).items()))})")
    print(f"  paired difference {args.cand} - {args.ref}: {statistics.mean(d):+.2f} points (se {se:.2f})")
    print(f"  improved/tied/worsened: {sum(x > 0 for x in d)}/{sum(x == 0 for x in d)}/{sum(x < 0 for x in d)}")
    worst = sorted(zip(d, keys))[:5]
    print("  worst regressions: " + "; ".join(f"{m} {x:+d}" for x, m in worst if x < 0))
    for v in (args.ref, args.cand):
        stats = [parse_stderr(rows[(v, m)].get("stderr")) for m in keys]
        stats = [(s, int(float(rows[(v, m)]["place"]))) for s, m in zip(stats, keys) if s is not None]
        if not stats:
            continue
        ends, reached, starts, soon, elim, errors = Counter(), [], 0, 0, 0, 0
        for s, place in stats:
            ends.update(s["ends"])
            reached += s["reached_hp"]
            starts += len(s["starts"])
            errors += s["errors"]
            if place > 1 and s["last_t"] is not None:
                elim += 1
                soon += any(0 <= s["last_t"] - t0 <= 10.0 for t0 in s["starts"])
        print(f"  {v}: stderr files {len(stats)}, cover starts {starts}, ends {dict(ends)}, "
              f"hp on first reaching cover {statistics.mean(reached) if reached else float('nan'):.2f} (n {len(reached)}), "
              f"eliminated within 10 s of a cover start {soon}/{elim}, error lines {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
