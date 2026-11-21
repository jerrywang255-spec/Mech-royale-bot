"""Synthetic exchange harness: the real Bot.act() against scripted hunters on a box-building arena.

NOT the official simulator and NOT a placement measurement. It exists because the repo has no match
runner. It answers one question: in fights we are losing, how long does the bot stay exposed, how much
damage does it take, and does it survive the next ~45 s? Every game constant below is an assumption
(see ASSUME); results only compare variants under the same assumptions, seeds and spawns.

    python scripts/exchange_sim.py --variant base=bot.py:COVER=0 --variant cover=bot.py --seeds 0-39
    python scripts/exchange_sim.py --variant orig=<baseline>/bot.py --variant off=bot.py:COVER=0 --seeds 0-9

The first variant is the reference for paired comparisons. Hits deal expected damage (damage x hit
probability), but the bot's own time-budgeted work (zone Field growth, cover search) depends on host
speed, so repeated runs can differ slightly; compare variants within one run.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import math
import os
import random
import statistics
import sys
import tempfile
import time
from collections import deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)  # nav.py and mechfight.py
import mechfight as mf  # noqa: E402

DT = 0.1
T0 = 100.0              # match clock at scenario start (past the early farming window)
DURATION = 45.0
SIZE = 512.0
CELL = 4.0
ASSUME = {
    "max_hp": 2000.0, "regen_delay_s": 5.0, "regen_hps": 45.0,    # bot.py docstring: 45 HP/s after 5 s
    "walk": 11.7, "boost_mult": 1.6, "boost_drain": 30.0, "energy_regen": 8.0, "turn_rate": 2.5,
    "rear_mult": 1.5, "front_half_arc_deg": 90.0, "vision_range": 250.0, "fov_deg": 120.0,
    "near_range": 30.0, "gun_dmg": 40.0, "gun_period": 0.2, "gun_mag": 12, "gun_reload": 2.0,
    "gun_range": 230.0, "enemy_speed": 10.5, "enemy_dmg": 60.0, "enemy_period": 0.5,
    "enemy_pref_range": 90.0, "enemy_omniscient_every_s": 3.0, "zone_dps": 30.0, "mech_radius": 2.0,
}
STATS = {"walk_speed": 11.7, "eye_height": 8, "muzzle_height": 7, "height": 9, "min_pitch": -0.7, "max_pitch": 1.3}
SCENARIOS = {
    # n enemies, enemy hit prob at <=100 m, our hit prob at <=80 m, enemy hp, our starting hp fraction, zone
    "outgunned1": dict(n=1, pe0=0.8, pu0=0.15, ehp=2000.0, hp0=1.0, zone="static"),
    "squad3": dict(n=3, pe0=0.5, pu0=0.35, ehp=1500.0, hp0=1.0, zone="static"),
    "winning1": dict(n=1, pe0=0.25, pu0=0.8, ehp=700.0, hp0=1.0, zone="static"),
    # duels we can win: do repeated retreats give them up? (exp. 1 watch-item)
    "even1": dict(n=1, pe0=0.45, pu0=0.45, ehp=1500.0, hp0=1.0, zone="static"),
    "edge1": dict(n=1, pe0=0.55, pu0=0.6, ehp=1200.0, hp0=1.0, zone="static"),
    "rotate2": dict(n=2, pe0=0.55, pu0=0.3, ehp=1500.0, hp0=0.9, zone="rotate"),
    # --- situations for the five-experiment review. zone="rand": a per-seed closing circle that a straight
    # walk at walk speed only just beats (1-10 s of slack), so departure timing decides zone damage.
    "zonequiet": dict(n=0, pe0=0.0, pu0=0.0, ehp=1.0, hp0=1.0, zone="rand", zdps=60.0),
    "zoneclose2": dict(n=2, pe0=0.5, pu0=0.3, ehp=1500.0, hp0=0.9, zone="rand", zdps=60.0),
    "kite1": dict(n=1, pe0=0.35, pu0=0.3, ehp=1500.0, hp0=1.0, zone="rand", zdps=60.0, kite=True),
    # hunters that out-shoot us while the circle closes: the cover hold and the zone deadline collide here
    "zonepin2": dict(n=2, pe0=0.8, pu0=0.15, ehp=2000.0, hp0=0.8, zone="rand", zdps=60.0),
}


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------- arena
def make_arena(work, seed, n_buildings=55):
    d = os.path.join(work, f"arena_{seed}")
    rng = random.Random(1000 + seed)
    half, rects, tries = SIZE / 2, [], 0
    while len(rects) < n_buildings and tries < 20000:
        tries += 1
        w, h = rng.uniform(12, 40), rng.uniform(12, 40)
        x0, y0 = rng.uniform(-half + 20, half - 20 - w), rng.uniform(-half + 20, half - 20 - h)
        r = (x0, y0, x0 + w, y0 + h, rng.uniform(10, 25))
        if all(r[2] + 10 < q[0] or q[2] + 10 < r[0] or r[3] + 10 < q[1] or q[3] + 10 < r[1] for q in rects):
            rects.append(r)
    v2 = os.path.join(d, "v2")
    if not os.path.exists(os.path.join(v2, "world.json")):
        os.makedirs(v2, exist_ok=True)
        n = int(SIZE)
        np.zeros((n, n), dtype="<i2").tofile(os.path.join(v2, "terrain.i16"))
        prisms = [{"kind": "building", "ring": [a, b, c, b, c, e, a, e], "base": 0.0, "top": t} for a, b, c, e, t in rects]
        with open(os.path.join(v2, "world.json"), "w") as f:
            json.dump({"terrain": {"nx": n, "ny": n, "minX": -half, "minY": -half, "step": 1.0,
                                   "file": "terrain.i16", "scale": 0.01}, "prisms": prisms}, f)
    return d, rects


class World:
    def __init__(self, rects):
        self.rects = rects
        r = ASSUME["mech_radius"]
        self.fat = [(a - r, b - r, c + r, e + r) for a, b, c, e, _ in rects]
        self.n = int(SIZE / CELL)
        half = SIZE / 2
        self.blocked = [False] * (self.n * self.n)
        for j in range(self.n):
            for i in range(self.n):
                x, y = -half + (i + 0.5) * CELL, -half + (j + 0.5) * CELL
                self.blocked[j * self.n + i] = (i in (0, self.n - 1) or j in (0, self.n - 1) or
                                                any(a <= x <= c and b <= y <= e for a, b, c, e in self.fat))
        self._bfs_cache = {}

    def solid(self, x, y):
        h = SIZE / 2 - 3
        if abs(x) > h or abs(y) > h:
            return True
        return any(a <= x <= c and b <= y <= e for a, b, c, e in self.fat)

    def clear(self, a, b):
        """Exact segment test against box prisms (ground at 0)."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        lo_x, hi_x, lo_y, hi_y = min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])
        for x0, y0, x1, y1, top in self.rects:
            if x1 < lo_x or x0 > hi_x or y1 < lo_y or y0 > hi_y:
                continue
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

    def sees(self, eye_xy, tgt_xy):
        """Mech eye (8 m) to the target's mid (4.5 m) or top (7.5 m)."""
        e = (eye_xy[0], eye_xy[1], 8.0)
        return self.clear(e, (tgt_xy[0], tgt_xy[1], 7.5)) or self.clear(e, (tgt_xy[0], tgt_xy[1], 4.5))

    def move(self, p, v):
        nx, ny = p[0] + v[0] * DT, p[1] + v[1] * DT
        if not self.solid(nx, ny):
            return [nx, ny]
        if not self.solid(nx, p[1]):
            return [nx, p[1]]
        if not self.solid(p[0], ny):
            return [p[0], ny]
        return [p[0], p[1]]

    def cell(self, x, y):
        i, j = int((x + SIZE / 2) // CELL), int((y + SIZE / 2) // CELL)
        return max(0, min(self.n - 1, i)), max(0, min(self.n - 1, j))

    def path_dir(self, src, dst):
        """Unit step from src toward dst along a BFS field (8-neighbor, no corner cutting)."""
        key = self.cell(*dst)
        dist = self._bfs_cache.get(key)
        if dist is None:
            n, bl = self.n, self.blocked
            dist = [-1] * (n * n)
            q = deque([key[1] * n + key[0]])
            dist[q[0]] = 0
            while q:
                c = q.popleft()
                j, i = divmod(c, n)
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < n and 0 <= jj < n:
                        k = jj * n + ii
                        if dist[k] < 0 and not bl[k] and not (di and dj and (bl[j * n + ii] or bl[jj * n + i])):
                            dist[k] = dist[c] + 1
                            q.append(k)
            if len(self._bfs_cache) > 400:
                self._bfs_cache.clear()
            self._bfs_cache[key] = dist
        n = self.n
        i, j = self.cell(*src)
        best, bv = None, dist[j * n + i] if dist[j * n + i] >= 0 else 1 << 30
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                ii, jj = i + di, j + dj
                if 0 <= ii < n and 0 <= jj < n and 0 <= dist[jj * n + ii] < bv:
                    best, bv = (ii, jj), dist[jj * n + ii]
        tx, ty = (dst if best is None else (-SIZE / 2 + (best[0] + 0.5) * CELL, -SIZE / 2 + (best[1] + 0.5) * CELL))
        dx, dy = tx - src[0], ty - src[1]
        m = math.hypot(dx, dy) or 1.0
        return dx / m, dy / m


# ---------------------------------------------------------------- scenario
def spawn(world, seed, n):
    rng = random.Random(seed * 7919 + n)
    for _ in range(4000):
        me = [rng.uniform(-90, 90), rng.uniform(-90, 90)]
        if world.solid(me[0], me[1]) or world.solid(me[0] + 3, me[1]) or world.solid(me[0], me[1] + 3):
            continue
        if n == 0:
            return me, []
        foes = []
        base = rng.uniform(-math.pi, math.pi)
        for k in range(n):
            for _ in range(400):
                a = base + k * (2 * math.pi / 3) * (1 if n > 1 else 0) + rng.uniform(-0.6, 0.6)
                r = rng.uniform(80, 160)
                p = [me[0] + math.cos(a) * r, me[1] + math.sin(a) * r]
                if not world.solid(*p):
                    foes.append(p)
                    break
        if len(foes) == n and world.sees(foes[0], me):
            return me, foes
    raise RuntimeError("no spawn")


def zone_for(kind, seed=0, me=None):
    if kind == "rand":
        return zone_rand(seed, me)
    if kind == "rotate":
        return {"center": [0.0, 0.0], "radius": 300.0, "next_center": [140.0, 60.0], "next_radius": 90.0,
                "shrink_start": T0 + 5.0, "shrink_end": T0 + 35.0, "dps": 5}
    return {"center": [0.0, 0.0], "radius": 400.0, "next_center": [0.0, 0.0], "next_radius": 300.0,
            "shrink_start": T0 + 500.0, "shrink_end": T0 + 600.0, "dps": 5}


def zone_now(z, t):
    ss, se = z["shrink_start"], z["shrink_end"]
    f = 0.0 if t <= ss else min(1.0, (t - ss) / (se - ss))
    c = [z["center"][i] + (z["next_center"][i] - z["center"][i]) * f for i in range(2)]
    return c, z["radius"] + (z["next_radius"] - z["radius"]) * f


def seed_radius(r):
    return r * 0.6 if r > 40 else max(r * 0.5, 6.0)      # same rule the bot uses for "safe enough"


def edge_time(z, me, t):
    """Seconds until the closing circle excludes `me` if we stand still (0 = already outside, inf = never)."""
    for k in range(0, 161):
        c, r = zone_now(z, t + k * 0.5)
        if math.dist(c, me) > r:
            return k * 0.5
    return math.inf


def straight_slack(z, me, t0=T0, speed=None):
    """Longest start delay (s) after which a straight walk to the next circle's seed area never leaves the
    moving circle. -1 = even leaving at once fails; inf = already inside the seed area."""
    speed = speed or ASSUME["walk"]
    nc, nr = z["next_center"], z["next_radius"]
    d = math.dist(me, nc)
    sr = seed_radius(nr)
    if d <= sr:
        return math.inf
    ux, uy = (nc[0] - me[0]) / d, (nc[1] - me[1]) / d
    best = -1.0
    for k in range(0, 241):                      # start delays up to 120 s
        delay = k * 0.5
        ok = True
        for j in range(0, 481):                  # horizon 240 s
            tau = j * 0.5
            walked = max(0.0, tau - delay) * speed
            walked = min(walked, d - sr)
            p = (me[0] + ux * walked, me[1] + uy * walked)
            c, r = zone_now(z, t0 + tau)
            if math.dist(c, p) > r:
                ok = False
                break
            if walked >= d - sr:
                break
        if ok:
            best = delay
        else:
            break
    return best


def zone_rand(seed, me):
    """Random closing circle (starting circle covers the arena) with 1-10 s of straight-walk slack from `me`."""
    rng = random.Random(seed * 104729 + 7)
    z = None
    for _ in range(600):
        r0, nr = rng.uniform(230.0, 300.0), rng.uniform(45.0, 110.0)
        ang, off = rng.uniform(-math.pi, math.pi), rng.uniform(0.0, r0 - nr - 15.0)
        hold, shrink = rng.uniform(2.0, 10.0), rng.uniform(14.0, 36.0)
        z = {"center": [0.0, 0.0], "radius": r0, "next_center": [math.cos(ang) * off, math.sin(ang) * off],
             "next_radius": nr, "shrink_start": T0 + hold, "shrink_end": T0 + hold + shrink, "dps": 5}
        s = straight_slack(z, me)
        if 1.0 <= s <= 10.0:
            z["slack0"] = s
            return z
    z["slack0"] = straight_slack(z, me)
    return z


def hello(map_dir, seed):
    return {"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": seed, "map": {"dir": map_dir, "files": []},
            "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0},
                       "vision": {"range": 250, "fov_deg": 120, "near_range": 30, "canopy_blocks": True,
                                  "show_enemy_hp": True},
                       "weapons": {"gun": {}, "laser": {}, "lightning": {},
                                   "mortar": {"gravity": 22.0, "splash_radius": 7.0}}}}


def run_episode(Bot, map_dir, world, scen_name, seed):
    sc = SCENARIOS[scen_name]
    A = ASSUME
    me, foes = spawn(world, seed, sc["n"])
    z = zone_for(sc["zone"], seed, me)
    zdps = sc.get("zdps", A["zone_dps"])
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        bot = Bot(hello(map_dir, seed))
    hp, mx = A["max_hp"] * sc["hp0"], A["max_hp"]
    energy, vel = 100.0, [0.0, 0.0]
    yaw = (math.atan2(foes[0][1] - me[1], foes[0][0] - me[0]) + 1.2) if foes else 0.0
    pitch = 0.0
    gun = {"cd": 0.0, "ammo": A["gun_mag"], "reload": 0.0}
    enemies = [{"id": 1 + k, "pos": list(p), "vel": [0.0, 0.0], "hp": sc["ehp"], "cd": 0.3 * k, "sign": 1 - 2 * (k % 2)}
               for k, p in enumerate(foes)]
    known, last_omni, last_dmg = list(me), T0, -99.0
    events, t = [], T0
    rec = {"scenario": scen_name, "seed": seed, "survived": True, "t_alive": DURATION, "taken": 0.0, "dealt": 0.0,
           "kills": 0, "exposed_s": 0.0, "losing_s": 0.0, "zone_dmg": 0.0, "min_hp": hp / mx, "regen_hp": 0.0,
           "disengage_ts": [], "slack0": z.get("slack0"), "late_s": 0.0, "thin_s": 0.0, "combat_thin_s": 0.0,
           "min_margin": math.inf, "pursuit_s": 0.0, "pursuit_thin_s": 0.0, "pursuit_ts": [], "fired": 0,
           "fired_muzzle_blocked": 0, "fired_eye_ok_muzzle_blocked": 0, "heavy_ts": [], "engage_hp": [],
           "zone_dmg_at_death": 0.0, "done_enemy_hp_left": None}
    prev_label = ""
    prev_d_next = math.dist(me, z["next_center"])
    last_vis_t, prev_vis_n, heavy_on = -99.0, 0, False
    hist = deque()              # (t, label, fired, exposed, hp, pos) for the death window
    win = deque()               # (t, taken, dealt) for the sim-side losing-exchange metric
    zwin = deque()              # (t, zone damage) for "zone was still hurting at death"
    lat = []
    tick = 0
    while t < T0 + DURATION - 1e-9:
        tick += 1
        live = [e for e in enemies if e["hp"] > 0]
        seen_by = [e for e in live if math.dist(e["pos"], me) <= 230 and world.sees(e["pos"], me)]
        exposed = bool(seen_by)
        # ---- observation
        vis = []
        for e in live:
            d = math.dist(e["pos"], me)
            b = math.atan2(e["pos"][1] - me[1], e["pos"][0] - me[0])
            in_fov = abs(wrap(b - yaw)) <= math.radians(A["fov_deg"] / 2) or d <= A["near_range"]
            if d <= A["vision_range"] and in_fov and world.sees(me, e["pos"]):
                ka = math.atan2(known[1] - e["pos"][1], known[0] - e["pos"][0])
                vis.append({"id": e["id"], "kind": "mech", "pos": [e["pos"][0], e["pos"][1], 0.0],
                            "vel": [e["vel"][0], e["vel"][1], 0.0], "aim": [ka, 0.0], "hp": e["hp"],
                            "max_hp": sc["ehp"], "weapons": ["gun"], "charging": False})
        c, r = zone_now(z, t)
        in_zone = math.dist(c, me) <= r
        obs = {"type": "obs", "tick": tick, "t": round(t, 3), "alive": 9 + len(live),
               "self": {"id": 0, "pos": [me[0], me[1], 0.0], "vel": [vel[0], vel[1], 0.0], "aim": [yaw, pitch],
                        "hp": hp, "max_hp": mx, "energy": energy, "max_energy": 100.0, "grounded": True, "cubes": 0,
                        "upgrades": [], "in_zone": in_zone, "stats": dict(STATS),
                        "weapons": [{"kind": "gun", "cooldown": gun["cd"], "ammo": gun["ammo"], "reload": gun["reload"],
                                     "stats": {"range": A["gun_range"], "speed": 252, "mag": A["gun_mag"]}}, None]},
               "mechs": vis, "projectiles": [], "crates": [], "cubes": [], "pickups": [], "sounds": [], "beams": [],
               "events": events, "zone": dict(z), "offer": None, "drops": []}
        events = []
        t1 = time.perf_counter()
        with contextlib.redirect_stderr(buf):
            act = bot.act(obs) or {}
        lat.append((time.perf_counter() - t1) * 1000)
        dbg = mf.draw.take() or {}
        label = str(dbg.get("label", "")).split(" hp ")[0]
        # ---- apply our action
        mv = act.get("move") or [0.0, 0.0]
        m = math.hypot(mv[0], mv[1])
        if m > 1:
            mv = [mv[0] / m, mv[1] / m]
            m = 1.0
        boosting = bool(act.get("boost")) and energy > 0 and m > 0.05
        sp = A["walk"] * (A["boost_mult"] if boosting else 1.0)
        energy = max(0.0, min(100.0, energy + (-A["boost_drain"] if boosting else A["energy_regen"]) * DT))
        vel = [mv[0] * sp, mv[1] * sp]
        me = world.move(me, vel)
        aim = act.get("aim") or [yaw, pitch]
        dy_ = wrap(aim[0] - yaw)
        step = A["turn_rate"] * DT
        yaw = wrap(yaw + max(-step, min(step, dy_)))
        pitch = aim[1]
        gun["cd"] = max(0.0, gun["cd"] - DT)
        if gun["reload"] > 0:
            gun["reload"] = max(0.0, gun["reload"] - DT)
            if gun["reload"] == 0:
                gun["ammo"] = A["gun_mag"]
        if act.get("reload") and gun["reload"] == 0 and gun["ammo"] < A["gun_mag"]:
            gun["reload"] = A["gun_reload"]
        fired = False
        fire = act.get("fire") or [False, False]
        if fire and fire[0] and gun["cd"] == 0 and gun["ammo"] > 0 and gun["reload"] == 0:
            fired = True
            rec["fired"] += 1
            # exp. 4: a shot is geometry-wasted when something is inside the aim tolerance but every bullet line
            # (muzzle 7 m -> target top/mid) is blocked. Eye-clear = the bot's own 8 m sight line was clear.
            mz = (me[0], me[1], 7.0)
            in_tol = []
            for e in live:
                d_ = math.dist(e["pos"], me)
                err_ = abs(wrap(yaw - math.atan2(e["pos"][1] - me[1], e["pos"][0] - me[0])))
                if d_ <= A["gun_range"] and err_ <= math.atan2(2.5, d_) + 0.005:
                    in_tol.append((e, world.clear(mz, (e["pos"][0], e["pos"][1], 4.5))
                                   or world.clear(mz, (e["pos"][0], e["pos"][1], 7.5))))
            if in_tol and not any(ok for _, ok in in_tol):
                rec["fired_muzzle_blocked"] += 1
                rec["fired_eye_ok_muzzle_blocked"] += any(world.sees(me, e["pos"]) for e, _ in in_tol)
            gun["cd"], gun["ammo"] = A["gun_period"], gun["ammo"] - 1
            if gun["ammo"] == 0:
                gun["reload"] = A["gun_reload"]
            best = None
            for e in live:
                d = math.dist(e["pos"], me)
                err = abs(wrap(yaw - math.atan2(e["pos"][1] - me[1], e["pos"][0] - me[0])))
                if d <= A["gun_range"] and err <= math.atan2(2.5, d) + 0.005 and world.sees(me, e["pos"]):
                    if best is None or err < best[0]:
                        best = (err, e, d)
            if best:
                _, e, d = best
                amt = A["gun_dmg"] * sc["pu0"] * min(1.0, 80.0 / max(d, 1.0))
                e["hp"] -= amt
                rec["dealt"] += amt
                win.append((t, 0.0, amt))
                events.append({"type": "hit", "target": e["id"], "amount": amt, "weapon": "gun"})
                if e["hp"] <= 0:
                    rec["kills"] += 1
                    events.append({"type": "kill", "target": e["id"]})
        # ---- enemies: shared vision plus periodic hunting knowledge of our position
        live = [e for e in enemies if e["hp"] > 0]
        if any(world.sees(e["pos"], me) for e in live) or t - last_omni >= A["enemy_omniscient_every_s"]:
            known = list(me)
            if t - last_omni >= A["enemy_omniscient_every_s"]:
                last_omni = t
        for e in live:
            d = math.dist(e["pos"], me)
            los = d <= 230 and world.sees(e["pos"], me)
            if los:
                b = math.atan2(me[1] - e["pos"][1], me[0] - e["pos"][0])
                if sc.get("kite"):     # keeps 140-200 m away from us: backs off when closer, never closes in
                    radial = -1.0 if d < 140.0 else (0.0 if d < 200.0 else 0.6)
                else:
                    radial = 1.0 if d > A["enemy_pref_range"] + 15 else (-1.0 if d < A["enemy_pref_range"] - 25 else 0.0)
                if int((t - T0) / 2.0) % 2:
                    e["sign"] = -e["sign"] if (int((t - T0) * 10) % 20) == 0 else e["sign"]
                sx, sy = -math.sin(b) * e["sign"] * 0.5, math.cos(b) * e["sign"] * 0.5
                vx, vy = math.cos(b) * radial + sx, math.sin(b) * radial + sy
            else:
                vx, vy = world.path_dir(e["pos"], known)
            n_ = math.hypot(vx, vy) or 1.0
            e["vel"] = [vx / n_ * A["enemy_speed"], vy / n_ * A["enemy_speed"]] if n_ > 0.05 else [0.0, 0.0]
            e["pos"] = world.move(e["pos"], e["vel"])
            e["cd"] = max(0.0, e["cd"] - DT)
            if los and e["cd"] == 0:
                e["cd"] = A["enemy_period"]
                amt = A["enemy_dmg"] * sc["pe0"] * min(1.0, 100.0 / max(d, 1.0))
                b_src = math.atan2(e["pos"][1] - me[1], e["pos"][0] - me[0])
                rear = abs(wrap(b_src - yaw)) > math.radians(A["front_half_arc_deg"])
                if rear:
                    amt *= A["rear_mult"]
                hp -= amt
                rec["taken"] += amt
                last_dmg = t
                win.append((t, amt, 0.0))
                ev = {"type": "damaged", "amount": amt, "weapon": "gun", "bearing": b_src, "by": e["id"]}
                if rear:
                    ev["rear"] = True
                events.append(ev)
        if not in_zone:
            amt = zdps * DT
            hp -= amt
            rec["taken"] += amt
            rec["zone_dmg"] += amt
            zwin.append((t, amt))
            last_dmg = t
            events.append({"type": "damaged", "amount": amt, "weapon": "zone", "bearing": None, "by": None})
        if hp > 0 and t - last_dmg >= A["regen_delay_s"] and hp < mx:
            add = min(mx - hp, A["regen_hps"] * DT)
            hp += add
            rec["regen_hp"] += add
        # ---- metrics
        while win and t - win[0][0] > 3.0:
            win.popleft()
        while zwin and t - zwin[0][0] > 3.0:
            zwin.popleft()
        tk, dl = sum(w[1] for w in win), sum(w[2] for w in win)
        if tk >= 0.10 * mx and dl < 0.5 * tk:
            rec["losing_s"] += DT
        heavy = tk >= 0.25 * mx                       # exp. 1: rapid armor loss from mechs
        if heavy and not heavy_on:
            rec["heavy_ts"].append(round(t - T0, 1))
        heavy_on = heavy
        n_vis = len(vis)
        if n_vis and not prev_vis_n and t - last_vis_t >= 6.0:
            rec["engage_hp"].append(round(max(hp, 0.0) / mx, 2))     # armor when the next engagement starts
        if n_vis:
            last_vis_t = t
        prev_vis_n = n_vis
        # zone departure margin (exp. 2): seconds until the closing circle excludes us minus the straight-walk
        # time to the next circle's safe area. < 0 = even leaving now costs zone damage.
        margin = math.inf
        if sc["zone"] != "static" and t < z["shrink_end"] + 2.0:
            d_next = math.dist(me, z["next_center"])
            need = max(0.0, d_next - seed_radius(z["next_radius"]))
            if need > 0:
                et = edge_time(z, me, t)
                margin = et - need / A["walk"] if et != math.inf else math.inf
                rec["min_margin"] = min(rec["min_margin"], margin)
                progress = (prev_d_next - d_next) / DT
                if margin < 3.0:
                    rec["thin_s"] += DT
                    if label.startswith(("fight", "retreat", "cover", "avoid")):
                        rec["combat_thin_s"] += DT
                    if margin < 1.5 and progress < 0.4 * A["walk"]:
                        rec["late_s"] += DT                 # margin nearly gone and not heading for safety
            prev_d_next = d_next
        # exp. 3: pursuing = a "fight" tick moving toward a visible enemy while our hits did nothing for 3 s
        if label.startswith("fight") and vis:
            nv = min(vis, key=lambda v: math.dist(v["pos"][:2], me))
            dx, dy = nv["pos"][0] - me[0], nv["pos"][1] - me[1]
            dn = math.hypot(dx, dy) or 1.0
            if (vel[0] * dx + vel[1] * dy) / dn > 3.0 and dl <= 0.0:
                rec["pursuit_s"] += DT
                rec["pursuit_ts"].append(round(t - T0, 1))
                if margin < 6.0:
                    rec["pursuit_thin_s"] += DT
        rec["exposed_s"] += DT if exposed else 0.0
        rec["min_hp"] = min(rec["min_hp"], max(hp, 0.0) / mx)
        hist.append((t, label, fired, exposed, hp, tuple(me)))
        if label in ("retreat", "cover") and prev_label not in ("retreat", "cover"):
            rec["disengage_ts"].append(round(t - T0, 1))
        prev_label = label
        while hist and t - hist[0][0] > 5.0:
            hist.popleft()
        if hp <= 0:
            rec["survived"], rec["t_alive"] = False, round(t - T0 + DT, 1)
            rec["zone_dmg_at_death"] = round(sum(a for _, a in zwin), 1)
            n_ = len(hist) or 1
            rec["death_window"] = {
                "fight": round(sum(h[1].startswith("fight") for h in hist) / n_, 2),
                "retreat": round(sum(h[1].startswith("retreat") for h in hist) / n_, 2),
                "avoid": round(sum(h[1].startswith("avoid") for h in hist) / n_, 2),
                "cover": round(sum(h[1].startswith("cover") for h in hist) / n_, 2),
                "firing": round(sum(h[2] for h in hist) / n_, 2), "exposed": round(sum(h[3] for h in hist) / n_, 2),
                "moved_m": round(sum(math.dist(hist[k][5], hist[k - 1][5]) for k in range(1, len(hist))), 1),
                "hp_at_start": round(hist[0][4] / mx, 2)}
            break
        t += DT
    rec["final_hp"] = round(max(hp, 0.0) / mx, 3)
    rec["lat_ms"] = lat
    log_txt = buf.getvalue()
    rec["cover_starts"] = log_txt.count("COVER start")
    rec["cover_ends"] = [ln.split("reason=")[1].split()[0] for ln in log_txt.splitlines() if ln.startswith("COVER end")]
    rec["cover_start_hp"] = [float(ln.split(" hp=")[1].split()[0]) for ln in log_txt.splitlines()
                             if ln.startswith("COVER start")]
    rec["cover_reached_hp"] = [float(ln.split("reached_hp=")[1].split()[0]) for ln in log_txt.splitlines()
                               if ln.startswith("COVER end") and "reached_hp=" in ln and "reached_hp=None" not in ln]
    rec["cover_reached_s"] = [float(ln.split("reached_s=")[1].split()[0]) for ln in log_txt.splitlines()
                              if ln.startswith("COVER end") and "reached_s=" in ln and "reached_s=None" not in ln]
    rec["exceptions"] = log_txt.count("Traceback")
    for k in ("taken", "dealt", "exposed_s", "losing_s", "zone_dmg", "min_hp", "regen_hp", "late_s", "thin_s",
              "combat_thin_s", "pursuit_s", "pursuit_thin_s"):
        rec[k] = round(rec[k], 3)
    rec["min_margin"] = None if rec["min_margin"] == math.inf else round(rec["min_margin"], 2)
    rec["pursuit_ts"] = rec["pursuit_ts"][-40:]
    return rec


# ---------------------------------------------------------------- variants and reporting
def load_variant(spec):
    name, rest = spec.split("=", 1)
    path, _, attrs = rest.partition(":")
    mod_spec = importlib.util.spec_from_file_location(f"bot_variant_{name}", os.path.abspath(path))
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    cls = type(f"Bot_{name}", (mod.Bot,), {})
    for kv in filter(None, attrs.split(",")):
        k, v = kv.split("=", 1)
        setattr(cls, k, json.loads(v.lower()) if v.lower() in ("true", "false") else float(v))
    return name, cls


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


def situations(results, names, scen_names):
    """Per-variant frequency of the five reviewed situations (synthetic; counts, not placements)."""
    print("\n## Situations (per episode averages; 'zone deaths' = died with zone damage in the last 3 s)")
    print("| scenario | variant | n | exp1 heavy loss/ep | exp1 died <=10 s after | exp1 hp at next engagement | "
          "exp2 zone dmg %max | exp2 zone deaths | exp2 thin s | exp2 late s | exp2 combat-thin s | "
          "exp3 pursuit s | exp3 pursuit-thin s | exp3 died <=10 s after pursuit | exp4 shots | exp4 muzzle-blocked |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sc in scen_names + ["ALL"]:
        for v in names:
            rows = [r for r in results[v] if sc == "ALL" or r["scenario"] == sc]
            if not rows:
                continue
            mean = lambda k: statistics.mean(r[k] for r in rows)  # noqa: E731
            heavy = sum(len(r["heavy_ts"]) for r in rows)
            died_after_heavy = sum(any(0 <= r["t_alive"] - h <= 10.0 for h in r["heavy_ts"])
                                   for r in rows if not r["survived"])
            eng = [x for r in rows for x in r["engage_hp"]]
            zd = sum(1 for r in rows if not r["survived"] and r["zone_dmg_at_death"] > 0)
            died_after_pursuit = sum(bool(r["pursuit_ts"]) and 0 <= r["t_alive"] - r["pursuit_ts"][-1] <= 10.0
                                     for r in rows if not r["survived"])
            fired = sum(r["fired"] for r in rows)
            print(f"| {sc} | {v} | {len(rows)} | {heavy / len(rows):.2f} | {died_after_heavy} | "
                  f"{statistics.mean(eng) if eng else float('nan'):.2f} | {100 * mean('zone_dmg') / ASSUME['max_hp']:.1f} | "
                  f"{zd} | {mean('thin_s'):.1f} | {mean('late_s'):.2f} | {mean('combat_thin_s'):.1f} | "
                  f"{mean('pursuit_s'):.1f} | {mean('pursuit_thin_s'):.2f} | {died_after_pursuit} | {fired} | "
                  f"{sum(r['fired_muzzle_blocked'] for r in rows)} ({sum(r['fired_eye_ok_muzzle_blocked'] for r in rows)} eye-clear) |")


def report(results, names, scen_names):
    print("\n## Per-variant results (synthetic; not placement)")
    print("| scenario | variant | n | survived | mean t_alive s | taken %max | dealt | kills | exposed s | losing s | zone dmg | cover starts |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sc in scen_names + ["ALL"]:
        for v in names:
            rows = [r for r in results[v] if sc == "ALL" or r["scenario"] == sc]
            if not rows:
                continue
            mx = ASSUME["max_hp"]
            print(f"| {sc} | {v} | {len(rows)} | {sum(r['survived'] for r in rows)} | "
                  f"{statistics.mean(r['t_alive'] for r in rows):.1f} | {100 * statistics.mean(r['taken'] for r in rows) / mx:.0f} | "
                  f"{statistics.mean(r['dealt'] for r in rows):.0f} | {sum(r['kills'] for r in rows)} | "
                  f"{statistics.mean(r['exposed_s'] for r in rows):.1f} | {statistics.mean(r['losing_s'] for r in rows):.1f} | "
                  f"{statistics.mean(r['zone_dmg'] for r in rows):.0f} | {sum(r['cover_starts'] for r in rows)} |")
    print("\n## Death windows (last 5 s before elimination; fraction of ticks)")
    print("| variant | deaths | fight | retreat | avoid | cover | firing | exposed | moved m | hp at -5 s |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for v in names:
        dw = [r["death_window"] for r in results[v] if "death_window" in r]
        if dw:
            f = lambda k: statistics.mean(d[k] for d in dw)  # noqa: E731
            print(f"| {v} | {len(dw)} | {f('fight'):.2f} | {f('retreat'):.2f} | {f('avoid'):.2f} | {f('cover'):.2f} | "
                  f"{f('firing'):.2f} | {f('exposed'):.2f} | {f('moved_m'):.0f} | {f('hp_at_start'):.2f} |")
    ref = names[0]
    for v in names[1:]:
        print(f"\n## Paired: {v} vs {ref} (same seeds, spawns, arenas)")
        print("| scenario | pairs | mean d t_alive s | t_alive better/tied/worse | survival gained/lost | mean d taken %max | mean d kills |")
        print("|---|---:|---:|---|---|---:|---:|")
        for sc in scen_names + ["ALL"]:
            a = {(r["scenario"], r["seed"]): r for r in results[ref] if sc == "ALL" or r["scenario"] == sc}
            b = {(r["scenario"], r["seed"]): r for r in results[v] if sc == "ALL" or r["scenario"] == sc}
            keys = sorted(set(a) & set(b))
            if not keys:
                continue
            dt = [b[k]["t_alive"] - a[k]["t_alive"] for k in keys]
            better, worse = sum(d > 0.5 for d in dt), sum(d < -0.5 for d in dt)
            gain = sum(b[k]["survived"] and not a[k]["survived"] for k in keys)
            lost = sum(a[k]["survived"] and not b[k]["survived"] for k in keys)
            dtk = statistics.mean(100 * (b[k]["taken"] - a[k]["taken"]) / ASSUME["max_hp"] for k in keys)
            dk = statistics.mean(b[k]["kills"] - a[k]["kills"] for k in keys)
            print(f"| {sc} | {len(keys)} | {statistics.mean(dt):+.2f} | {better}/{len(keys) - better - worse}/{worse} | "
                  f"{gain}/{lost} | {dtk:+.1f} | {dk:+.2f} |")
        a = {(r["scenario"], r["seed"]): r for r in results[ref]}
        b = {(r["scenario"], r["seed"]): r for r in results[v]}
        worst = sorted(set(a) & set(b), key=lambda k: b[k]["t_alive"] - a[k]["t_alive"])[:5]
        print("worst regressions (scenario, seed, t_alive ref -> var, cover ends): " + "; ".join(
            f"{k[0]}/{k[1]} {a[k]['t_alive']:.1f}->{b[k]['t_alive']:.1f} {b[k]['cover_ends']}" for k in worst))
    print("\n## act() latency on this host (all ticks; not the 1-CPU container)")
    for v in names:
        lat = [x for r in results[v] for x in r["lat_ms"]]
        print(f"- {v}: ticks {len(lat)}, p50 {pct(lat, 50):.2f} p95 {pct(lat, 95):.2f} p99 {pct(lat, 99):.2f} "
              f"max {max(lat):.2f} ms, >40 ms {sum(x > 40 for x in lat)}, exceptions {sum(r['exceptions'] for r in results[v])}")
    for v in names:
        ends, reached, reached_s = [], [], []
        for r in results[v]:
            ends += r["cover_ends"]
            reached += r["cover_reached_hp"]
            reached_s += r["cover_reached_s"]
        deaths = [r for r in results[v] if not r["survived"]]
        soon = sum(any(0 <= r["t_alive"] - d <= 10.0 for d in r["disengage_ts"]) for r in deaths)
        print(f"- {v}: deaths {len(deaths)}, of which within 10 s of entering retreat/cover: {soon}; "
              f"disengage entries {sum(len(r['disengage_ts']) for r in results[v])}")
        if ends:
            from collections import Counter
            print(f"- {v} cover episode ends: {dict(Counter(ends))}; hp at start mean "
                  f"{statistics.mean(x for r in results[v] for x in r['cover_start_hp']):.2f}; first arrival at cover: hp mean "
                  f"{statistics.mean(reached) if reached else float('nan'):.2f}, after mean "
                  f"{statistics.mean(reached_s) if reached_s else float('nan'):.1f} s (n {len(reached)}); "
                  f"episodes never reaching cover {len(ends) - len(reached)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", action="append", required=True, help="name=path/to/bot.py[:ATTR=VAL,...]")
    ap.add_argument("--seeds", default="0-19")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--work-dir", default=os.path.join(tempfile.gettempdir(), "mr_exchange_sim"))
    ap.add_argument("--json", default=None, help="write per-episode rows (without latency) to this file")
    args = ap.parse_args()
    lo, _, hi = args.seeds.partition("-")
    seeds = list(range(int(lo), int(hi or lo) + 1))
    scen_names = [s for s in args.scenarios.split(",") if s]
    variants = [load_variant(s) for s in args.variant]
    names = [n for n, _ in variants]
    results = {n: [] for n in names}
    t_start = time.perf_counter()
    for seed in seeds:
        map_dir, rects = make_arena(args.work_dir, seed)
        world = World(rects)
        for sc in scen_names:
            for n, cls in variants:
                results[n].append(run_episode(cls, map_dir, world, sc, seed))
    print(f"seeds {seeds[0]}-{seeds[-1]} ({len(seeds)}), scenarios {scen_names}, variants {names}, "
          f"python {sys.version.split()[0]}, wall {time.perf_counter() - t_start:.0f} s")
    print("assumptions: " + json.dumps(ASSUME))
    report(results, names, scen_names)
    situations(results, names, scen_names)
    if args.json:
        with open(args.json, "w") as f:
            for n in names:
                for r in results[n]:
                    f.write(json.dumps({"variant": n, **{k: v for k, v in r.items() if k != "lat_ms"}}) + "\n")


if __name__ == "__main__":
    main()
