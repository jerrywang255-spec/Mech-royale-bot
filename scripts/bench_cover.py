"""Worst-case latency of the cover logic: act() on the tick a losing exchange starts a cover search.

    python scripts/bench_cover.py [--bot-dir .] [--trials 300] [--size 2048] [--buildings 400]

Per trial: a fresh Bot sharing one prebuilt Map, three shooters 120-250 m away, two hits (a losing
exchange), timing the second tick. Case "zone change" also replaces the next circle on that tick, so a
new zone Field is built in the same act() call (the cover search must defer instead of stacking).
Host-machine numbers, not the 1-CPU container.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_act  # noqa: E402

import time  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--size", type=float, default=2048.0)
    ap.add_argument("--buildings", type=int, default=400)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--arena-dir", default=os.path.join(tempfile.gettempdir(), "mr_bench_cover_arena"))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.bot_dir))
    B = importlib.import_module("bot")
    nav = importlib.import_module("nav")
    if not os.path.exists(os.path.join(args.arena_dir, "v2", "world.json")):
        bench_act.make_arena(args.arena_dir, args.size, args.buildings, args.seed)
    shared = nav.Map(args.arena_dir)
    cfg = {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0}, "vision": {"near_range": 30},
           "weapons": {"gun": {}, "laser": {}, "lightning": {}, "mortar": {"gravity": 22.0, "splash_radius": 7.0}}}
    rng = random.Random(args.seed)
    res = {"steady": [], "zone change": []}
    starts = {"steady": 0, "zone change": 0}
    search_all = []
    log = io.StringIO()
    s = args.size
    for trial in range(args.trials):
        case = "steady" if trial % 2 == 0 else "zone change"
        while True:
            me = (rng.uniform(-s * 0.3, s * 0.3), rng.uniform(-s * 0.3, s * 0.3))
            if shared.walkable(*me):
                break
        foes = []
        for k in range(3):
            a, r = rng.uniform(-math.pi, math.pi), rng.uniform(120, 250)
            p = (me[0] + math.cos(a) * r, me[1] + math.sin(a) * r)
            foes.append({"id": 100 + k, "kind": "mech", "pos": [p[0], p[1], 0.0], "vel": [0, 0, 0],
                         "aim": [math.atan2(me[1] - p[1], me[0] - p[0]), 0.0], "hp": 100, "max_hp": 100,
                         "weapons": ["gun"]})
        with contextlib.redirect_stderr(log):
            bot = B.Bot({"type": "hello", "slot": 0, "seed": trial, "map": {"dir": "/none", "files": []}, "config": cfg})
        bot.map = shared
        z1 = bench_act.zone((0, 0), s * 0.45, (0, 0), s * 0.40, 500, 600)
        z2 = bench_act.zone((0, 0), s * 0.45, (30, -20), s * 0.38, 500, 600) if case == "zone change" else z1
        for tick, z in ((10, z1), (12, z2)):
            o = bench_act.observation(tick, rng, me, z, s)
            o["mechs"] = [dict(f) for f in foes]
            o["self"]["hp"] = 94 if tick == 10 else 88
            o["events"] = [{"type": "damaged", "amount": 6, "weapon": "gun", "bearing": 0.0, "by": 100}]
            o["projectiles"] = []
            t0 = time.perf_counter()
            with contextlib.redirect_stderr(log):
                bot.act(o)
            ms = (time.perf_counter() - t0) * 1000
        res[case].append(ms)
        starts[case] += getattr(bot, "cover", None) is not None
        if case == "steady" and hasattr(bot, "_find_cover"):     # the search alone, found or not
            t0 = time.perf_counter()
            bot._find_cover(o, [tuple(f["pos"]) for f in foes], 0.014)
            search_all.append((time.perf_counter() - t0) * 1000)
    pct = bench_act.pct
    print(f"bot {os.path.abspath(args.bot_dir)}  python {sys.version.split()[0]}  arena {s:.0f} m / {args.buildings} buildings")
    for case, xs in res.items():
        print(f"{case:12s} trigger ticks {len(xs)}: p50 {pct(xs, 50):.2f} p95 {pct(xs, 95):.2f} p99 {pct(xs, 99):.2f} "
              f"max {max(xs):.2f} ms; >40 ms {sum(x > 40 for x in xs)}; >50 ms {sum(x > 50 for x in xs)}; "
              f"cover started {starts[case]}")
    print(f"logged act() failures {log.getvalue().count('act() failed')}, cover errors {log.getvalue().count('cover disabled')}")
    search = [float(ln.split(" ms=")[1]) for ln in log.getvalue().splitlines() if ln.startswith("COVER start")]
    if search:
        print(f"cover search on successful starts (COVER start ms=): n {len(search)} p50 {pct(search, 50):.2f} "
              f"p99 {pct(search, 99):.2f} max {max(search):.2f} ms")
    if search_all:
        print(f"_find_cover alone, 14 ms budget, found or not: n {len(search_all)} p50 {pct(search_all, 50):.2f} "
              f"p95 {pct(search_all, 95):.2f} p99 {pct(search_all, 99):.2f} max {max(search_all):.2f} ms")


if __name__ == "__main__":
    main()
