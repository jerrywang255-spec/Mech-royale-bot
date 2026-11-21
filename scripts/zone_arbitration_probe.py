"""When the bot is outside the circle (urgency 1.0), how often does combat arbitration point it away from safety?
(experiment 2, deterministic single-tick decisions, no map, no motion)

Random one-tick situations: 0-3 enemies at random bearings/ranges (most aiming at us, some charging a laser,
each with a hit event), an optional incoming mortar shell, three armor levels. Safety is the direction of the
next circle. A "flip" = the commanded move has a negative component toward safety; a "stall" = |move| < 0.3.
Variants are bot files, optionally with attribute overrides:  name=path[:ATTR=VAL,...]

    python scripts/zone_arbitration_probe.py --variant snapshot=.scratch/baseline/bot.py --variant current=bot.py
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
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
import synth  # noqa: E402

G = 22.0
CFG = {"mortar": {"gravity": G, "splash_radius": 7.0}, "gun": {}, "laser": {}, "lightning": {}}


def shell(land, t_land, height=30.0):
    vx = vy = 0.0
    vz = (0.5 * G * t_land ** 2 - height) / t_land
    return {"id": 900, "kind": "mortar", "pos": [land[0], land[1], height], "vel": [vx, vy, vz]}


def load(spec):
    name, rest = spec.split("=", 1)
    path, _, attrs = rest.partition(":")
    ms = importlib.util.spec_from_file_location("arb_" + name, os.path.abspath(path))
    mod = importlib.util.module_from_spec(ms)
    ms.loader.exec_module(mod)
    cls = type("B_" + name, (mod.Bot,), {})
    for kv in filter(None, attrs.split(",")):
        k, v = kv.split("=", 1)
        setattr(cls, k, v.lower() == "true" if v.lower() in ("true", "false") else float(v))
    return name, cls


def situation(rng):
    phi = rng.uniform(-math.pi, math.pi)
    z = synth.zone(center=(0, 0), radius=20, nc=(200 * math.cos(phi), 200 * math.sin(phi)), nr=60, ss=0.0, se=30.0)
    foes, events = [], []
    for k in range(rng.choice([0, 0, 1, 2, 3])):
        b, d = rng.uniform(-math.pi, math.pi), rng.uniform(40, 200)
        aim_at_us = rng.random() < 0.8
        e = synth.enemy(10 + k, (math.cos(b) * d, math.sin(b) * d),
                        aim=[math.atan2(-math.sin(b), -math.cos(b)) if aim_at_us else rng.uniform(-3, 3), 0.0],
                        charging=aim_at_us and rng.random() < 0.3, hp=rng.choice([30, 70, 100]))
        foes.append(e)
        if aim_at_us:
            events.append(synth.hit(10 + k, 10.0, bearing=b))
    proj = []
    has_shell = rng.random() < 0.4
    if has_shell:
        b, d = rng.uniform(-math.pi, math.pi), rng.uniform(3.0, 12.0)
        proj.append(shell((math.cos(b) * d, math.sin(b) * d), rng.uniform(0.5, 2.0)))
    hp = rng.choice([30, 60, 90])
    o = synth.obs(100, (0, 0), foes, hp=hp, z=z, in_zone=False, events=events, weapons=[synth.gun(), synth.laser()])
    o["projectiles"] = proj
    return o, (math.cos(phi), math.sin(phi)), has_shell, len(foes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", action="append", required=True)
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    variants = [load(v) for v in args.variant]
    res = {n: {"all": [0, 0, 0], "shell": [0, 0, 0], "noshell": [0, 0, 0], "foes": [0, 0, 0], "boost_fail": 0}
           for n, _ in variants}
    for n, cls in variants:
        rng = random.Random(args.seed)
        for _ in range(args.n):
            o, safe, has_shell, nf = situation(rng)
            with contextlib.redirect_stderr(io.StringIO()):
                b = cls(synth.hello("/nonexistent", weapons=CFG))
                a = b.act(o)
            mv = a["move"]
            toward = mv[0] * safe[0] + mv[1] * safe[1]
            flip, stall = toward < 0.0, math.hypot(*mv) < 0.3
            for key in ("all", "shell" if has_shell else "noshell") + (("foes",) if nf else ()):
                res[n][key][0] += 1
                res[n][key][1] += flip
                res[n][key][2] += stall
    print(f"{args.n} random one-tick situations, outside the circle (urgency 1.0); flip = move points away from safety")
    print("| variant | all: flips (stalls) | shell present | no shell | any enemy visible |")
    print("|---|---|---|---|---|")
    for n, _ in variants:
        r = res[n]
        cell = lambda k: f"{r[k][1]}/{r[k][0]} ({100 * r[k][1] / max(r[k][0], 1):.1f}%) ({r[k][2]})"  # noqa: E731
        print(f"| {n} | {cell('all')} | {cell('shell')} | {cell('noshell')} | {cell('foes')} |")


if __name__ == "__main__":
    main()
