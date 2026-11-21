"""How much time does the zone-departure rule leave? (experiment 2, no enemies, no map)

For random closing-circle configurations and random bot positions it compares, for a bot that stands still:
  * the LATEST start delay after which a straight walk at walk speed to the next circle's safe area never
    leaves the moving circle (`slack`, from exchange_sim.straight_slack), against
  * the first time Bot._urgency() reaches a threshold (`delay_u`).
margin = slack - delay_u. margin >= 0: the rule says "go" in time. margin < 0: it says "go" after the deadline.
Only observation fields the bot really receives are used (zone, position, walk_speed). Straight-line path:
the Field is not built, so detours around buildings (the bot's 1.4x factor) are not tested.

    python scripts/zone_margin_probe.py [--n 3000] [--seed 1] [--bot bot.py]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import math
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import exchange_sim as S  # noqa: E402

T0 = S.T0


def load_bot(path):
    spec = importlib.util.spec_from_file_location("probe_bot", os.path.abspath(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Bot


def make_obs(z, me, t):
    c, r = S.zone_now(z, t)
    return {"t": t, "tick": int(t * 10), "alive": 10,
            "self": {"pos": [me[0], me[1], 0.0], "vel": [0, 0, 0], "in_zone": math.dist(c, me) <= r,
                     "stats": dict(S.STATS), "hp": 100, "max_hp": 100, "energy": 100},
            "zone": z, "mechs": [], "events": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bot", default=os.path.join(ROOT, "bot.py"))
    ap.add_argument("--thresholds", default="0.5,0.7,0.85")
    args = ap.parse_args()
    Bot = load_bot(args.bot)
    thr = [float(x) for x in args.thresholds.split(",")]
    rng = random.Random(args.seed)
    with contextlib.redirect_stderr(io.StringIO()):
        bot = Bot(S.hello("/nonexistent", 1))
    rows = {u: [] for u in thr}
    feasible = infeasible = never = 0
    for _ in range(args.n):
        r0, nr = rng.uniform(150.0, 400.0), rng.uniform(40.0, 140.0)
        ang, off = rng.uniform(-math.pi, math.pi), rng.uniform(0.0, max(0.0, r0 - nr - 10.0))
        hold, shrink = rng.uniform(2.0, 20.0), rng.uniform(10.0, 45.0)
        z = {"center": [0.0, 0.0], "radius": r0, "next_center": [math.cos(ang) * off, math.sin(ang) * off],
             "next_radius": nr, "shrink_start": T0 + hold, "shrink_end": T0 + hold + shrink, "dps": 5}
        a, rr = rng.uniform(-math.pi, math.pi), r0 * math.sqrt(rng.uniform(0.0, 0.9))
        me = (math.cos(a) * rr, math.sin(a) * rr)
        if math.dist(me, z["next_center"]) <= S.seed_radius(nr) + 4:
            continue                                   # already safe: nothing to depart from
        slack = S.straight_slack(z, me)
        if slack < 0:
            infeasible += 1                            # even leaving at t0 is late: not a timing question
            continue
        feasible += 1
        first = {}
        t = T0
        while t < T0 + hold + shrink + 5.0 and len(first) < len(thr):
            u = bot._urgency(make_obs(z, me, t))
            for th in thr:
                if th not in first and u >= th:
                    first[th] = t - T0
            t = round(t + 0.1, 3)
        for th in thr:
            if th in first:
                rows[th].append((slack - first[th], slack, first[th], hold, shrink, math.dist(me, z["next_center"])))
            else:
                never += 1
                rows[th].append((-99.0, slack, math.inf, hold, shrink, math.dist(me, z["next_center"])))
    print(f"bot {args.bot}: {args.n} configs; already safe or skipped: {args.n - feasible - infeasible}; "
          f"feasible (slack >= 0): {feasible}; infeasible even leaving at t0: {infeasible}")
    print("| urgency threshold | n | margin < 0 (late) | margin < 2 s | median margin s | p10 margin s | "
          "median delay_u s | median slack s |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for th in thr:
        m = [r[0] for r in rows[th]]
        late = sum(x < 0 for x in m)
        thin = sum(x < 2.0 for x in m)
        dl = [r[2] for r in rows[th] if r[2] != math.inf]
        sl = [r[1] for r in rows[th]]
        m_sorted = sorted(m)
        print(f"| {th:.2f} | {len(m)} | {late} ({100 * late / max(len(m), 1):.1f}%) | {thin} ({100 * thin / max(len(m), 1):.1f}%) | "
              f"{statistics.median(m):.1f} | {m_sorted[len(m) // 10]:.1f} | {statistics.median(dl) if dl else float('nan'):.1f} | "
              f"{statistics.median(sl):.1f} |")
    worst = sorted(rows[thr[0]])[:5]
    print("worst margins at the first threshold (margin, slack, delay_u, hold, shrink, dist_to_next): "
          + "; ".join(f"({', '.join(f'{v:.1f}' for v in w)})" for w in worst))


if __name__ == "__main__":
    main()
