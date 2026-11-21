"""Worst-case act() latency on synthetic observations. Host-machine numbers, not the 1-CPU container.

    python scripts/bench_act.py [--bot-dir .] [--size 2048] [--ticks 900]

Scenario per tick: up to 15 visible mechs (30-250 m), crowded loot/sound/projectile lists, alternating
fight and retreat (retreat runs 16 hide-LOS checks), and two zone changes that rebuild the Dijkstra field.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import math
import os
import random
import sys
import tempfile
import time

import numpy as np

STATS = {"walk_speed": 11.7, "eye_height": 8, "muzzle_height": 7, "height": 9, "min_pitch": -0.7, "max_pitch": 1.3}


def make_arena(d, size, n_buildings, seed):
    v2 = os.path.join(d, "v2")
    os.makedirs(v2, exist_ok=True)
    n = int(size)
    np.zeros((n, n), dtype="<i2").tofile(os.path.join(v2, "terrain.i16"))
    rng, half, rects, tries = random.Random(seed), size / 2, [], 0
    while len(rects) < n_buildings and tries < 50000:
        tries += 1
        w, h = rng.uniform(12, 40), rng.uniform(12, 40)
        x0, y0 = rng.uniform(-half + 20, half - 20 - w), rng.uniform(-half + 20, half - 20 - h)
        r = (x0, y0, x0 + w, y0 + h, rng.uniform(8, 25))
        if all(r[2] + 10 < q[0] or q[2] + 10 < r[0] or r[3] + 10 < q[1] or q[3] + 10 < r[1] for q in rects):
            rects.append(r)
    prisms = [{"kind": "building", "ring": [a, b, c, b, c, e, a, e], "base": 0.0, "top": t} for a, b, c, e, t in rects]
    with open(os.path.join(v2, "world.json"), "w") as f:
        json.dump({"terrain": {"nx": n, "ny": n, "minX": -half, "minY": -half, "step": 1.0, "file": "terrain.i16",
                               "scale": 0.01}, "prisms": prisms}, f)


def weapons(rng):
    gun = {"kind": "gun", "cooldown": 0.0, "ammo": rng.randint(0, 12), "reload": 0.0,
           "stats": {"range": 230, "speed": 252, "mag": 12}}
    other = rng.choice([
        {"kind": "laser", "cooldown": 0.0, "charge": rng.choice([0.0, 0.5, 0.8]), "stats": {"range": 280, "charge": 0.75, "hold_s": 2}},
        {"kind": "mortar", "cooldown": 0.0, "stats": {"speed": 72, "gravity": 22}},
        {"kind": "lightning", "cooldown": 0.0, "stats": {"range": 45, "energy": 3, "cone_deg": 30}},
    ])
    return [gun, other]


def zone(c, r, nc, nr, ss, se):
    return {"center": list(c), "radius": r, "next_center": list(nc), "next_radius": nr,
            "shrink_start": ss, "shrink_end": se, "dps": 5}


def observation(tick, rng, me, z, size):
    t = tick / 10
    pos = lambda r0, r1: (lambda a, r: [me[0] + math.cos(a) * r, me[1] + math.sin(a) * r, 0.0])(
        rng.uniform(-math.pi, math.pi), rng.uniform(r0, r1))
    n_mechs = rng.choice([0, 3, 8, 15])
    mechs = [{"id": 100 + k, "kind": rng.choice(["mech", "mech", "npc"]), "pos": pos(30, 250),
              "vel": [rng.uniform(-10, 10), rng.uniform(-10, 10), 0], "aim": [rng.uniform(-3, 3), 0],
              "hp": rng.uniform(10, 100), "max_hp": 100, "weapons": ["gun", "laser"],
              "charging": rng.random() < 0.3} for k in range(n_mechs)]
    hurt = n_mechs and rng.random() < 0.6
    events = [{"type": "damaged", "amount": 8, "weapon": "gun", "bearing": 0.3, "by": 100}] if hurt else []
    return {"type": "obs", "tick": tick, "t": t, "alive": max(2, 16 - tick // 60),
            "self": {"id": 0, "pos": [me[0], me[1], 0.0], "vel": [0, 0, 0], "aim": [rng.uniform(-3, 3), 0.0],
                     "hp": 30 if tick % 2 else 90, "max_hp": 100, "energy": 60, "max_energy": 100, "grounded": True,
                     "cubes": 2, "upgrades": [], "weapons": weapons(rng), "in_zone": True, "stats": dict(STATS)},
            "mechs": mechs,
            "projectiles": [{"id": 500 + k, "kind": "mortar", "pos": pos(5, 120), "vel": [5, 5, 10]} for k in range(20)],
            "crates": [{"id": 600 + k, "pos": pos(10, 110), "hp": 20} for k in range(20)],
            "cubes": [{"id": 700 + k, "pos": pos(5, 70)} for k in range(30)],
            "pickups": [{"id": 800 + k, "kind": rng.choice(["laser", "lightning", "mortar"]), "pos": pos(2, 90)} for k in range(10)],
            "sounds": [{"kind": rng.choice(["gun", "step", "laser"]), "bearing": rng.uniform(-3, 3),
                        "dist": rng.choice(["near", "mid", "far"]), "age": 0.1} for _ in range(30)],
            "beams": [], "events": events, "zone": z,
            "offer": {"id": 1, "expires": 5, "choices": [{"id": "gyros", "name": "", "desc": ""}]} if tick % 50 == 0 else None,
            "drops": [{"id": 900 + k, "pos": pos(20, 140), "radius": 5, "lands_in": 3, "capture_s": 5, "progress": 0,
                       "holder": None} for k in range(4)]}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot-dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--size", type=float, default=2048.0)
    ap.add_argument("--buildings", type=int, default=400)
    ap.add_argument("--ticks", type=int, default=900)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--arena-dir", default=None,
                    help="write the arena here instead of a TemporaryDirectory (some sandboxes can't delete those)")
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.bot_dir))
    B = importlib.import_module("bot")
    rng = random.Random(args.seed)
    s = args.size
    phases = {0: zone((0, 0), s * 0.45, (60, -40), s * 0.30, 30, 60),
              300: zone((60, -40), s * 0.30, (20, 10), s * 0.15, 45, 75),
              600: zone((20, 10), s * 0.15, (0, 0), s * 0.05, 70, 85)}
    arena = contextlib.nullcontext(args.arena_dir) if args.arena_dir else tempfile.TemporaryDirectory()
    with arena as d:
        make_arena(d, s, args.buildings, args.seed)
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            # `weapons` is required by the schema; without it act() raised on every tick at the mortar dodge.
            bot = B.Bot({"type": "hello", "slot": 0, "seed": 1, "map": {"dir": d, "files": []},
                         "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0},
                                    "vision": {"near_range": 30},
                                    "weapons": {"gun": {}, "laser": {}, "lightning": {},
                                                "mortar": {"gravity": 22.0, "splash_radius": 7.0}}}})
        me, z, times, errs = (10.0, 10.0), phases[0], [], 0
        for tick in range(args.ticks):
            z = phases.get(tick, z)
            o = observation(tick, rng, me, z, s)
            t0 = time.perf_counter()
            try:
                with contextlib.redirect_stderr(log):
                    bot.act(o)
            except Exception:
                errs += 1
            times.append((time.perf_counter() - t0) * 1000)
    logged = log.getvalue().count("act() failed")
    print(f"bot {os.path.abspath(args.bot_dir)}  python {sys.version.split()[0]}  arena {s:.0f} m / {args.buildings} buildings")
    print(f"ticks {len(times)}: p50 {pct(times, 50):.2f} p95 {pct(times, 95):.2f} p99 {pct(times, 99):.2f} "
          f"max {max(times):.2f} ms; >40 ms {sum(t > 40 for t in times)}; >50 ms {sum(t > 50 for t in times)}; "
          f"raised {errs}; logged failures {logged}")


if __name__ == "__main__":
    main()
