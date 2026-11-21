"""Summarize an exchange_sim.py --json file with paired uncertainty (synthetic proxies, not placement).

    python scripts/summarize_sim.py runs.jsonl --ref base --cand final [--ref2 snap]
        [--contested outgunned1,squad3,rotate2,zoneclose2,zonepin2,kite1] [--controls winning1,even1]

Contested scenarios are ones where the bot is under pressure; controls are duels it should win or ignore,
so a change that fires there is a regression signal.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ref", default="base")
    ap.add_argument("--cand", default="final")
    ap.add_argument("--ref2", default=None, help="optional second reference (e.g. an older snapshot)")
    ap.add_argument("--contested", default="outgunned1,squad3,rotate2,zoneclose2,zonepin2,kite1")
    ap.add_argument("--controls", default="winning1,even1")
    args = ap.parse_args()
    rows = [json.loads(line) for line in open(args.file)]
    by = {}
    for r in rows:
        by.setdefault(r["variant"], {})[(r["scenario"], r["seed"])] = r
    cont, ctrl = set(args.contested.split(",")), set(args.controls.split(","))

    def pair(ref, cand, scen):
        a, b = by[ref], by[cand]
        ks = sorted(k for k in a if k in b and k[0] in scen)
        d = [b[k]["t_alive"] - a[k]["t_alive"] for k in ks]
        se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
        return ks, d, se

    def row(v, scen):
        rs = [by[v][k] for k in by[v] if k[0] in scen]
        n = len(rs)
        return (f"| {v} | {n} | {sum(r['survived'] for r in rs)} | {statistics.mean(r['t_alive'] for r in rs):.1f} | "
                f"{sum(r['t_alive'] < 20 for r in rs)} | {sum(r['zone_dmg'] > 0 for r in rs)} | "
                f"{sum((not r['survived']) and r['zone_dmg_at_death'] > 0 for r in rs)} | "
                f"{statistics.mean(r['kills'] for r in rs):.2f} | "
                f"{100 * statistics.mean(r['dealt'] for r in rs) / 2000:.1f} |")

    variants = [v for v in (args.ref2, args.ref, args.cand) if v]
    for title, scen in (("contested", cont), ("controls", ctrl)):
        print(f"\n### {title}: {sorted(scen)}")
        print("| variant | episodes | survived 45 s | mean life s | died < 20 s | zone-damage eps | zone deaths | kills/ep | dealt %max |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for v in variants:
            print(row(v, scen))
    print("\n### paired differences in time alive (s); better/tied/worse use a 0.5 s tie band")
    print("| pair | scope | n | mean d | SE | better/tied/worse | survival gained/lost |")
    print("|---|---|---:|---:|---:|---|---|")
    for ref, cand in [(args.ref, args.cand)] + ([(args.ref2, args.cand)] if args.ref2 else []):
        for title, scen in (("contested", cont), ("controls", ctrl)):
            ks, d, se = pair(ref, cand, scen)
            g = sum(by[cand][k]["survived"] and not by[ref][k]["survived"] for k in ks)
            lo = sum(by[ref][k]["survived"] and not by[cand][k]["survived"] for k in ks)
            print(f"| {cand} vs {ref} | {title} | {len(ks)} | {statistics.mean(d):+.2f} | {se:.2f} | "
                  f"{sum(x > 0.5 for x in d)}/{sum(abs(x) <= 0.5 for x in d)}/{sum(x < -0.5 for x in d)} | {g}/{lo} |")
    print("\n### per scenario, final vs ref")
    print("| scenario | n | mean d life s (SE) | better/tied/worse | survived ref->cand | kills ref->cand |")
    print("|---|---:|---:|---|---|---|")
    for sc in sorted(cont | ctrl):
        ks, d, se = pair(args.ref, args.cand, {sc})
        if not ks:
            continue
        print(f"| {sc} | {len(ks)} | {statistics.mean(d):+.2f} ({se:.2f}) | "
              f"{sum(x > 0.5 for x in d)}/{sum(abs(x) <= 0.5 for x in d)}/{sum(x < -0.5 for x in d)} | "
              f"{sum(by[args.ref][k]['survived'] for k in ks)}->{sum(by[args.cand][k]['survived'] for k in ks)} | "
              f"{sum(by[args.ref][k]['kills'] for k in ks)}->{sum(by[args.cand][k]['kills'] for k in ks)} |")
    ks, d, se = pair(args.ref, args.cand, cont)
    worst = sorted(zip(d, ks))[:5]
    print("\nworst 5 regressions (scenario/seed: ref -> cand life s): " + "; ".join(
        f"{k[0]}/{k[1]} {by[args.ref][k]['t_alive']:.1f}->{by[args.cand][k]['t_alive']:.1f}" for _, k in worst))
    fin = [by[args.cand][k] for k in by[args.cand] if k[0] in cont]
    bas = [by[args.ref][k] for k in by[args.ref] if k[0] in cont]
    hv = lambda rs: (sum(len(r["heavy_ts"]) for r in rs),  # noqa: E731
                     sum(any(0 <= r["t_alive"] - h <= 10 for h in r["heavy_ts"]) for r in rs if not r["survived"]))
    eb = [v for r in bas for v in r["engage_hp"]]
    ef = [v for r in fin for v in r["engage_hp"]]
    print(f"\nexp 1 diagnostics (contested): heavy-loss events / deaths within 10 s of one: ref {hv(bas)}, cand {hv(fin)}")
    print(f"  armor fraction at the next engagement: ref {statistics.mean(eb):.3f} (n={len(eb)}), "
          f"cand {statistics.mean(ef):.3f} (n={len(ef)})")
    ends = {e: sum(r["cover_ends"].count(e) for r in fin) for e in ("exposed", "stuck", "healed", "zone", "timeout")}
    print(f"  cover episodes (cand): {sum(r['cover_starts'] for r in fin)}, ends {ends}")


if __name__ == "__main__":
    main()
