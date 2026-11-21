import subprocess, sys, json
sys.path.insert(0, "tests")
from synth import obs, zone, enemy, hit
p = subprocess.Popen([sys.executable, "-u", "bot.py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
hello = {"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": 1, "map": {"dir": "/nonexistent", "files": []},
         "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0}, "vision": {"near_range": 30},
                    "zone": {"start_radius": 400, "phases": [{"hold_s": 30, "shrink_s": 20, "radius": 200, "dps": 5}]},
                    "drops": {"times": [60], "repair": 0.5}}}
p.stdin.write(json.dumps(hello) + "\n"); p.stdin.flush()
print("ready line:", p.stdout.readline().strip())
bad = 0
for t in range(1, 8):
    o = obs(t, (0, 0), [enemy(5, (80, 0))], events=[hit(5, 10.0)], z=zone(radius=400, nr=300))
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
    try:
        json.loads(p.stdout.readline())
    except Exception:
        bad += 1
p.stdin.write(json.dumps({"type": "end", "place": 9, "kills": 0}) + "\n"); p.stdin.flush()
out, err = p.communicate(timeout=20)
print("exit code:", p.returncode, "| non-JSON answer lines:", bad, "| leftover stdout:", repr(out.strip()))
print("stderr line kinds:", [l.split(" ")[0] + (" " + l.split(" ")[1] if l.startswith("CONFIG") else "") for l in err.splitlines()])
print("SITREP line:", next((l for l in err.splitlines() if l.startswith("SITREP")), "MISSING")[:300])
print("tracebacks in stderr:", err.count("Traceback"))
