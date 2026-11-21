import sys, math, random, contextlib, io
sys.path.insert(0, "scripts"); sys.path.insert(0, "tests"); sys.path.insert(0, ".")
import zone_arbitration_probe as P, synth
variants = [P.load(v) for v in ("snapshot=.scratch/baseline/bot.py", "current_old=bot.py:ZONE_FIRST=false",
                                 "dedupe_only=bot.py:ZONE_FIRST_URGENCY=9", "zonefirst=bot.py")]
print("blast exposure: we walk the commanded move at 11.7 m/s until the shell lands; inside splash (7 m)?")
print("| variant | shell situations | inside splash at landing | mean distance to impact m | flips away from safety |")
print("|---|---:|---:|---:|---:|")
for name, cls in variants:
    rng = random.Random(11)
    n = inside = flips = 0; dsum = 0.0
    for _ in range(3000):
        o, safe, has_shell, nf = P.situation(rng)
        if not has_shell: continue
        with contextlib.redirect_stderr(io.StringIO()):
            b = cls(synth.hello("/nonexistent", weapons=P.CFG)); a = b.act(o)
        sh = o["projectiles"][0]
        g = P.G; px, py, pz = sh["pos"]; vz = sh["vel"][2]
        # time until ground: pz + vz t - g t^2/2 = 0
        disc = vz * vz + 2 * g * pz
        tl = (vz + math.sqrt(disc)) / g
        ix, iy = px + sh["vel"][0] * tl, py + sh["vel"][1] * tl
        mv = a["move"]; sp = 11.7 * (1.6 if a.get("boost") else 1.0)
        mx_, my_ = mv[0] * sp * tl, mv[1] * sp * tl
        d = math.hypot(mx_ - ix, my_ - iy)
        n += 1; inside += d <= 7.0; dsum += d
        flips += (mv[0] * safe[0] + mv[1] * safe[1]) < 0
    print(f"| {name} | {n} | {inside} ({100*inside/n:.1f}%) | {dsum/n:.1f} | {flips} ({100*flips/n:.1f}%) |")
