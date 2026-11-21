import sys, math, contextlib, io, importlib.util
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
from synth import enemy, hit, make_bot, obs, quiet, zone
G = 22.0
def shell(i, land, t_land, height=30.0, speed_xy=(20.0, 0.0)):
    vx, vy = speed_xy
    px, py = land[0] - vx * t_land, land[1] - vy * t_land
    vz = (0.5 * G * t_land ** 2 - height) / t_land
    return {"id": i, "kind": "mortar", "pos": [px, py, height], "vel": [vx, vy, vz]}
cfg = {"mortar": {"gravity": G, "splash_radius": 7.0}, "gun": {}, "laser": {}, "lightning": {}}
# We stand outside the closing circle; safety is +x (next circle center 200 m east). Urgency is 1.0.
z = zone(center=(0, 0), radius=20, nc=(200, 0), nr=60, ss=0.0, se=30.0)
def run(label, foes, projectiles, events):
    for path in ("bot.py", ".scratch/baseline/bot.py"):
        spec = importlib.util.spec_from_file_location("b_" + path.replace("/", "_").replace(".", "_"), path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        import synth; synth.B = mod
        b = synth.make_bot(weapons=cfg)
        o = obs(100, (0, 0), foes, hp=70, z=z, in_zone=False, events=events)
        o["projectiles"] = projectiles
        a, _ = quiet(b.act, o)
        print(f"{label:46s} {'current ' if path=='bot.py' else 'snapshot'} move=({a['move'][0]:+.2f},{a['move'][1]:+.2f}) toward-zone(+x)={'YES' if a['move'][0] > 0.3 else 'NO '} urgency={b._urgency(o):.2f}")
foe_east = enemy(5, (60, 0), aim=[math.pi, 0.0])
run("no other pressure", [], [], [])
run("enemy shooting us from the safe side", [foe_east], [], [hit(5, 10.0, bearing=0.0)])
run("mortar shell lands 4 m east in 0.8 s", [], [shell(1, (4.0, 0.0), 0.8)], [])
run("enemy from safe side + shell from safe side", [foe_east], [shell(1, (4.0, 0.0), 0.8)], [hit(5, 10.0, bearing=0.0)])
foe_c = enemy(6, (60, 0), aim=[math.pi, 0.0], charging=True)
run("enemy charging a laser at us from the safe side", [foe_c], [], [hit(6, 10.0, bearing=0.0)])
