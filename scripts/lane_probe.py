"""Does the bot's sight check say "clear" when the muzzle's own line is blocked? (experiment 4, no motion)

For random shooter/target pairs on box-building arenas it compares the bot's decision (`Bot._can_see`,
which is what gates direct fire: eye 8 m -> target centre 4.5 m on the 2 m grid, ignoring geometry within
3 m of either end) with exact geometry from the MUZZLE (7 m) to the target's top (7.5 m) or centre (4.5 m).
No movement happens between the decision and the check, so this isolates the sight-check error from
simulation ordering. Pairs are split by whether the shooter stands within 3 m of a building face (the
"muzzle behind the corner" case).

    python scripts/lane_probe.py [--arenas 12] [--pairs 2500] [--bot bot.py]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import exchange_sim as S  # noqa: E402


def wall_gap(x, y, rects):
    best = 1e9
    for x0, y0, x1, y1, _ in rects:
        best = min(best, math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1)))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arenas", type=int, default=12)
    ap.add_argument("--pairs", type=int, default=2500)
    ap.add_argument("--bot", default=os.path.join(ROOT, "bot.py"))
    ap.add_argument("--work-dir", default=os.path.join(ROOT, ".scratch", "sim"))
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("lane_bot", os.path.abspath(args.bot))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tally = {k: {"pairs": 0, "bot_clear": 0, "bot_clear_muzzle_blocked": 0, "muzzle_clear": 0,
                 "muzzle_clear_bot_blocked": 0} for k in ("wall-adjacent (<3 m)", "open (>=3 m)")}
    for a in range(args.arenas):
        d, rects = S.make_arena(args.work_dir, 700 + a)
        world = S.World(rects)
        with contextlib.redirect_stderr(io.StringIO()):
            bot = mod.Bot(S.hello(d, a))
        rng = random.Random(5000 + a)
        done = 0
        while done < args.pairs:
            sx, sy = rng.uniform(-200, 200), rng.uniform(-200, 200)
            if world.solid(sx, sy):
                continue
            near = wall_gap(sx, sy, rects) < 3.0
            # half the samples are forced next to a wall face so the near-wall bucket is well populated
            if (done % 2 == 0) != near:
                continue
            ang, dist = rng.uniform(-math.pi, math.pi), rng.uniform(30, 230)
            tx, ty = sx + math.cos(ang) * dist, sy + math.sin(ang) * dist
            if abs(tx) > 240 or abs(ty) > 240 or world.solid(tx, ty):
                continue
            me = {"pos": [sx, sy, 0.0], "stats": dict(S.STATS)}
            other = {"kind": "mech", "pos": [tx, ty, 0.0]}
            bot_clear = bot._can_see(me, other)
            mz = (sx, sy, 7.0)
            muzzle_clear = world.clear(mz, (tx, ty, 4.5)) or world.clear(mz, (tx, ty, 7.5))
            k = tally["wall-adjacent (<3 m)" if near else "open (>=3 m)"]
            k["pairs"] += 1
            k["bot_clear"] += bot_clear
            k["bot_clear_muzzle_blocked"] += bot_clear and not muzzle_clear
            k["muzzle_clear"] += muzzle_clear
            k["muzzle_clear_bot_blocked"] += muzzle_clear and not bot_clear
            done += 1
    print(f"bot {args.bot}; {args.arenas} arenas x {args.pairs} pairs each (synthetic box buildings 10-25 m, flat ground)")
    print("| shooter position | pairs | bot says clear | ...but muzzle line blocked | rate | muzzle clear | ...but bot says blocked |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for name, k in tally.items():
        rate = 100 * k["bot_clear_muzzle_blocked"] / max(k["bot_clear"], 1)
        print(f"| {name} | {k['pairs']} | {k['bot_clear']} | {k['bot_clear_muzzle_blocked']} | {rate:.1f}% | "
              f"{k['muzzle_clear']} | {k['muzzle_clear_bot_blocked']} |")


if __name__ == "__main__":
    main()
