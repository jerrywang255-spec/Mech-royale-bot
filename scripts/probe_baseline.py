"""Synthetic probes of the bot's code paths. NOT a match simulator.

Builds a made-up arena (flat terrain, random box buildings), then measures:
  1. grid LOS (nav.Map.los) versus exact box geometry, and what that does to firing;
  2. act() on observations with optional fields omitted;
  3. a few fixed decision scenarios (multi-enemy, retreat threshold, zone vs retreat);
  4. act() / Field latency on this machine.

Results describe code behavior on synthetic inputs, not real placements. Usage:
    python scripts/probe_baseline.py [--size 1024] [--buildings 120]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import random
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Mech-royale-bot"))
import bot as B  # noqa: E402
import nav  # noqa: E402

GUN = {"kind": "gun", "cooldown": 0.0, "ammo": 12, "reload": 0.0,
       "stats": {"range": 230, "speed": 252, "mag": 12}}
LASER = {"kind": "laser", "cooldown": 0.0, "charge": 0.0,
         "stats": {"range": 280, "charge": 0.75, "hold_s": 2}}
STATS = {"walk_speed": 11.7, "eye_height": 8, "muzzle_height": 7, "height": 9,
         "min_pitch": -0.7, "max_pitch": 1.3}


# ---------------------------------------------------------------- arena
def make_arena(d, size, n_buildings, seed):
    v2 = os.path.join(d, "v2")
    os.makedirs(v2, exist_ok=True)
    n = int(size)
    np.zeros((n, n), dtype="<i2").tofile(os.path.join(v2, "terrain.i16"))
    rng = random.Random(seed)
    half = size / 2
    rects, tries = [], 0
    while len(rects) < n_buildings and tries < 20000:
        tries += 1
        w, h = rng.uniform(12, 40), rng.uniform(12, 40)
        x0, y0 = rng.uniform(-half + 20, half - 20 - w), rng.uniform(-half + 20, half - 20 - h)
        r = (x0, y0, x0 + w, y0 + h, rng.uniform(8, 25))
        if any(not (r[2] + 10 < q[0] or q[2] + 10 < r[0] or r[3] + 10 < q[1] or q[3] + 10 < r[1]) for q in rects):
            continue  # keep >= 10 m streets
        rects.append(r)
    prisms = [{"kind": "building", "ring": [a, b, c, b, c, e, a, e], "base": 0.0, "top": top}
              for a, b, c, e, top in rects]
    world = {"terrain": {"nx": n, "ny": n, "minX": -half, "minY": -half, "step": 1.0,
                         "file": "terrain.i16", "scale": 0.01}, "prisms": prisms}
    with open(os.path.join(v2, "world.json"), "w") as f:
        json.dump(world, f)
    return rects


def exact_los(a, b, rects):
    """Segment a->b against box prisms (ground at 0): Liang-Barsky clip, then height test."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    for x0, y0, x1, y1, top in rects:
        t0, t1, ok = 0.0, 1.0, True
        for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
            if p == 0:
                if q < 0:
                    ok = False
                    break
            else:
                r = q / p
                if p < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
                if t0 > t1:
                    ok = False
                    break
        if ok and min(a[2] + dz * t0, a[2] + dz * t1) < top:
            return False
    return True


def wall_gap(x, y, rects):
    best = 1e9
    for x0, y0, x1, y1, _ in rects:
        dx = max(x0 - x, 0.0, x - x1)
        dy = max(y0 - y, 0.0, y - y1)
        best = min(best, math.hypot(dx, dy))
    return best


def free_point(rng, rects, half, margin=3.0):
    while True:
        x, y = rng.uniform(-half + 30, half - 30), rng.uniform(-half + 30, half - 30)
        if wall_gap(x, y, rects) >= margin:
            return x, y


# ---------------------------------------------------------------- observations
def hello(map_dir):
    return {"type": "hello", "slot": 0, "seed": 1, "map": {"dir": map_dir, "files": []},
            "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0}}}


def zone(center=(0, 0), radius=500, nc=(0, 0), nr=300, ss=60.0, se=120.0):
    return {"center": list(center), "radius": radius, "next_center": list(nc), "next_radius": nr,
            "shrink_start": ss, "shrink_end": se, "dps": 5}


def enemy(i, pos, hp=100, **kw):
    e = {"id": i, "kind": "mech", "pos": [pos[0], pos[1], 0.0], "vel": [0, 0, 0], "aim": [0, 0],
         "hp": hp, "max_hp": 100, "weapons": ["gun"]}
    e.update(kw)
    return e


def obs(tick, me, enemies=(), hp=100, z=None, aim=(0.0, 0.0), events=()):
    return {"type": "obs", "tick": tick, "t": tick / 10, "alive": 16,
            "self": {"id": 0, "pos": [me[0], me[1], 0.0], "vel": [0, 0, 0], "aim": list(aim), "hp": hp,
                     "max_hp": 100, "energy": 100, "max_energy": 100, "grounded": True, "cubes": 0,
                     "upgrades": [], "weapons": [dict(GUN), dict(LASER)], "in_zone": True, "stats": dict(STATS)},
            "mechs": [dict(e) for e in enemies], "projectiles": [], "crates": [], "cubes": [], "pickups": [],
            "sounds": [], "beams": [], "events": list(events), "zone": z or zone(), "offer": None, "drops": []}


def quiet_bot(map_dir):
    with contextlib.redirect_stderr(io.StringIO()):
        return B.Bot(hello(map_dir))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


# ---------------------------------------------------------------- probes
def probe_los(m, rects, half, n=4000, seed=3):
    rng = random.Random(seed)
    buckets = {"near_wall": [0, 0, 0], "open": [0, 0, 0]}  # [exact clear, grid blocked among those, grid clear but exact blocked]
    example = None
    for _ in range(n):
        sx, sy = free_point(rng, rects, half)
        ang, dist = rng.uniform(-math.pi, math.pi), rng.uniform(20, 200)
        tx, ty = sx + math.cos(ang) * dist, sy + math.sin(ang) * dist
        if abs(tx) > half - 10 or abs(ty) > half - 10 or wall_gap(tx, ty, rects) < 3.0:
            continue
        a, b = (sx, sy, 8.0), (tx, ty, 4.5)   # bot eye, target center (Bot._can_see heights)
        ex, gr = exact_los(a, b, rects), m.los(a, b)
        c = m.cell(sx, sy)
        key = "near_wall" if c >= 0 and m.cost[c] > 1.2 else "open"
        if ex:
            buckets[key][0] += 1
            if not gr:
                buckets[key][1] += 1
                if example is None and key == "near_wall":
                    example = ((sx, sy), (tx, ty))
        elif gr:
            buckets[key][2] += 1
    return buckets, example


def probe_fire(map_dir, example):
    """Enemy present in obs['mechs'] with exact clear LOS; does _weapons fire the gun?"""
    (sx, sy), (tx, ty) = example
    b = quiet_bot(map_dir)
    e = enemy(5, (tx, ty))
    o = obs(1, (sx, sy), [e])
    yaw, pitch, _ = b._weapons(o, e, "fight")
    o["self"]["aim"] = [yaw, pitch]           # pretend the torso is already lined up
    _, _, fire_blocked = b._weapons(o, e, "fight")
    b.map = None                              # same tick, map LOS ignored
    _, _, fire_nomap = b._weapons(o, e, "fight")
    return fire_blocked, fire_nomap, math.dist((sx, sy), (tx, ty))


def probe_missing_fields():
    b = quiet_bot("/nonexistent")
    out = {}
    cases = {
        "enemy without hp": {k: v for k, v in enemy(5, (50, 0)).items() if k != "hp"},
        "enemy without hp/max_hp": {k: v for k, v in enemy(5, (50, 0)).items() if k not in ("hp", "max_hp")},
        "enemy without weapons": {k: v for k, v in enemy(5, (50, 0)).items() if k != "weapons"},
    }
    for name, e in cases.items():
        try:
            b.act(obs(10, (0, 0), [e]))
            out[name] = "ok"
        except Exception as ex:  # noqa: BLE001
            out[name] = f"{type(ex).__name__}: {ex}"
    return out


def probe_decisions():
    b = quiet_bot("/nonexistent")
    res = {}
    three = [enemy(5, (50, 0)), enemy(6, (0, 55)), enemy(7, (-45, 10))]
    res["3 visible enemies <60 m, own hp 100%"] = b._decide(obs(10, (0, 0), three), three, [], 1.0)[1]
    one = [enemy(5, (50, 0))]
    res["1 enemy 50 m, own hp 34%"] = b._decide(obs(10, (0, 0), one), one, [], 0.34)[1]
    res["1 enemy 50 m, own hp 36%"] = b._decide(obs(10, (0, 0), one), one, [], 0.36)[1]
    one_far = [enemy(5, (150, 0))]
    hit = [{"type": "damaged", "amount": 10, "weapon": "gun", "bearing": 0.0, "by": 5}]
    o = obs(10, (0, 0), one_far, events=hit)
    b._remember(o)
    res["1 enemy 150 m shooting us, own hp 40%"] = b._decide(o, one_far, [], 0.40)[1]

    # zone vs retreat: next circle centered at origin, bot between attacker and the outer ring
    rows = []
    for label, me, foe, z, tick in (
        ("early: r=200, next r=100, bot x=90, attacker x=40", (90, 0), (40, 0),
         zone(radius=200, nr=100, ss=40.0, se=100.0), 100),
        ("late: shrinking 60->30, bot x=40, attacker x=10", (40, 0), (10, 0),
         zone(radius=60, nr=30, ss=5.0, se=35.0), 100),
    ):
        bb = quiet_bot("/nonexistent")
        ev = [{"type": "damaged", "amount": 10, "weapon": "gun", "bearing": math.pi, "by": 5}]
        a = bb.act(obs(tick, me, [enemy(5, foe)], hp=30, z=z, events=ev))
        radial = a["move"][0]  # +x = away from both the zone center and toward the outer ring
        rows.append((label, round(radial, 2), a["boost"]))
    return res, rows


def probe_latency(map_dir, rects, half, size):
    b = quiet_bot(map_dir)
    rng = random.Random(11)
    times, field_t = [], []
    me = free_point(rng, rects, half)
    z = zone(radius=size * 0.45, nr=size * 0.30, ss=60.0, se=120.0)
    for tick in range(600):
        if tick == 300:   # phase change: new next circle -> new Field
            z = zone(center=(0, 0), radius=size * 0.30, nr=size * 0.15, nc=(40, -30), ss=90.0, se=140.0)
        foes = []
        if tick % 3:
            for k in range(3):   # visible enemies 30-250 m away
                a, r = rng.uniform(-math.pi, math.pi), rng.uniform(30, 250)
                foes.append(enemy(10 + k, (me[0] + math.cos(a) * r, me[1] + math.sin(a) * r),
                                  hp=rng.choice([40, 100])))
        hp = 30 if tick % 2 else 90   # alternate retreat (16 hide LOS checks) and fight
        ev = [{"type": "damaged", "amount": 5, "weapon": "gun", "bearing": 0.0, "by": 10}] if foes else []
        o = obs(tick, me, foes, hp=hp, z=z, events=ev)
        key = (round(z["next_center"][0]), round(z["next_center"][1]), round(z["next_radius"]))
        busy = b.field is None or b.field_key != key or not b.field.done   # budgeted Field work this tick
        t0 = time.perf_counter()
        b.act(o)
        ms = (time.perf_counter() - t0) * 1000
        (field_t if busy else times).append(ms)
    return times, field_t


def probe_field(m, size):
    rows = []
    for r in (30, 60, 120, 240, 360):
        if r > size * 0.45:
            continue
        t0 = time.perf_counter()
        f = nav.Field(m, (0.0, 0.0), r)
        init_ms = (time.perf_counter() - t0) * 1000
        seeds = len(f.heap)
        calls, t1 = 0, time.perf_counter()
        while not f.done:
            f.step(0.018)
            calls += 1
        rows.append((r, seeds, round(init_ms, 1), calls, round(time.perf_counter() - t1, 2)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=float, default=1024.0)
    ap.add_argument("--buildings", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    half = args.size / 2
    print(f"python {sys.version.split()[0]}, numpy {np.__version__}, arena {args.size:.0f} m, seed {args.seed}")
    with tempfile.TemporaryDirectory() as d:
        rects = make_arena(d, args.size, args.buildings, args.seed)
        t0 = time.perf_counter()
        m = nav.Map(d)
        print(f"buildings {len(rects)}, grid {m.nx}x{m.ny}, Map build {time.perf_counter() - t0:.2f}s")

        print("\n[1] grid LOS vs exact box LOS (eye 8 m -> target 4.5 m, 20-200 m)")
        buckets, ex = probe_los(m, rects, half)
        for k, (clear, fn, fp) in buckets.items():
            print(f"  shooter {k:9s}: exact-clear {clear:5d}, grid says blocked {fn:4d} "
                  f"({100 * fn / max(clear, 1):.1f}%), grid-clear-but-blocked {fp}")
        if ex:
            fb, fn_, dd = probe_fire(d, ex)
            print(f"  example near-wall pair, {dd:.0f} m, aim lined up: fire with map {fb}, without map {fn_}")

        print("\n[2] act() with optional ActorObs fields omitted")
        for k, v in probe_missing_fields().items():
            print(f"  {k:26s}: {v}")

        print("\n[3] decisions")
        res, rows = probe_decisions()
        for k, v in res.items():
            print(f"  {k:40s}: {v}")
        for label, radial, boost in rows:
            print(f"  hp 30%, {label}: move.x {radial:+.2f} (+ = outward), boost {boost}")

        print("\n[4] latency on this machine (not the 1-CPU container)")
        times, field_t = probe_latency(d, rects, half, args.size)
        for name, xs in (("decision-only ticks", times), ("ticks with Field init/step", field_t)):
            if xs:
                print(f"  {name:27s} n={len(xs):3d}: p50 {pct(xs, 50):.1f} p95 {pct(xs, 95):.1f} "
                      f"p99 {pct(xs, 99):.1f} max {max(xs):.1f} ms; >50 ms: {sum(t > 50 for t in xs)}")
        print("  Field(seed radius): seeds, init ms, step(18 ms) calls to finish, total s")
        for r, seeds, init_ms, calls, tot in probe_field(m, args.size):
            print(f"    r={r:3d} m: {seeds:5d} seeds, init {init_ms:6.1f} ms, {calls:3d} ticks, {tot:.2f} s")


if __name__ == "__main__":
    main()
