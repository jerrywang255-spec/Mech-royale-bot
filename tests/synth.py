"""Small synthetic hellos, observations and arenas for deterministic tests (no private seeds)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bot as B  # noqa: E402

STATS = {"walk_speed": 11.7, "eye_height": 8, "muzzle_height": 7, "height": 9,
         "min_pitch": -0.7, "max_pitch": 1.3}


def gun(**kw):
    w = {"kind": "gun", "cooldown": 0.0, "ammo": 12, "reload": 0.0,
         "stats": {"range": 230, "speed": 252, "mag": 12}}
    w.update(kw)
    return w


def laser(**kw):
    w = {"kind": "laser", "cooldown": 0.0, "charge": 0.0,
         "stats": {"range": 280, "charge": 0.75, "hold_s": 2}}
    w.update(kw)
    return w


def hello(map_dir="/nonexistent", **cfg):
    config = {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0},
              "vision": {"range": 250, "fov_deg": 120, "near_range": 30, "canopy_blocks": True,
                         "show_enemy_hp": True}}
    config.update(cfg)
    return {"type": "hello", "slot": 0, "seed": 1, "map": {"dir": map_dir, "files": []}, "config": config}


def make_bot(map_dir="/nonexistent", **cfg):
    with contextlib.redirect_stderr(io.StringIO()):
        return B.Bot(hello(map_dir, **cfg))


def zone(center=(0, 0), radius=500, nc=(0, 0), nr=300, ss=60.0, se=120.0):
    return {"center": list(center), "radius": radius, "next_center": list(nc), "next_radius": nr,
            "shrink_start": ss, "shrink_end": se, "dps": 5}


def enemy(i, pos, hp=100, **kw):
    e = {"id": i, "kind": "mech", "pos": [float(pos[0]), float(pos[1]), 0.0], "vel": [0, 0, 0],
         "aim": [0.0, 0.0], "hp": hp, "max_hp": 100, "weapons": ["gun"]}
    e.update(kw)
    return e


def hit(by, amount=10.0, bearing=0.0, weapon="gun"):
    return {"type": "damaged", "amount": amount, "weapon": weapon, "bearing": bearing, "by": by}


def obs(tick, me, enemies=(), hp=100, z=None, aim=(0.0, 0.0), events=(), weapons=None, alive=16,
        in_zone=True, energy=100):
    return {"type": "obs", "tick": tick, "t": tick / 10, "alive": alive,
            "self": {"id": 0, "pos": [float(me[0]), float(me[1]), 0.0], "vel": [0, 0, 0], "aim": list(aim),
                     "hp": hp, "max_hp": 100, "energy": energy, "max_energy": 100, "grounded": True,
                     "cubes": 0, "upgrades": [], "in_zone": in_zone, "stats": dict(STATS),
                     "weapons": weapons if weapons is not None else [gun(), None]},
            "mechs": [dict(e) for e in enemies], "projectiles": [], "crates": [], "cubes": [], "pickups": [],
            "sounds": [], "beams": [], "events": list(events), "zone": z or zone(), "offer": None, "drops": []}


def quiet(fn, *a, **kw):
    """Call fn with stderr captured; returns (result, captured_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def make_arena(d, rects, size=512):
    """Flat square arena with box buildings: rects = [(x0, y0, x1, y1, top), ...]."""
    v2 = os.path.join(d, "v2")
    os.makedirs(v2, exist_ok=True)
    n = int(size)
    np.zeros((n, n), dtype="<i2").tofile(os.path.join(v2, "terrain.i16"))
    prisms = [{"kind": "building", "ring": [a, b, c, b, c, e, a, e], "base": 0.0, "top": top}
              for a, b, c, e, top in rects]
    world = {"terrain": {"nx": n, "ny": n, "minX": -size / 2, "minY": -size / 2, "step": 1.0,
                         "file": "terrain.i16", "scale": 0.01}, "prisms": prisms}
    with open(os.path.join(v2, "world.json"), "w") as f:
        json.dump(world, f)
    return d
