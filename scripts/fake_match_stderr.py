"""Produce stderr files from scripted (fake) matches to exercise summarize_stderr.py. NOT a simulator.

    python scripts/fake_match_stderr.py <out_dir> [n_matches]
"""
import json
import os
import queue
import random
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT = os.path.join(ROOT, "Mech-royale-bot", "bot.py")
sys.path.insert(0, os.path.join(ROOT, "Mech-royale-bot", "tests"))
from synth import enemy, hit, obs, zone  # noqa: E402


def match(seed):
    rng = random.Random(seed)
    msgs = [{"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": seed, "map": {"dir": "/none", "files": []},
             "config": {"mech": {"step_up": 1.2}, "loot": {"pickup_radius": 4.0}, "vision": {"near_range": 30}}}]
    hp, ticks = 100.0, rng.randint(600, 1500)
    for tick in range(1, ticks):
        ev = []
        if rng.random() < 0.05:
            amt = rng.uniform(5, 20)
            hp = max(1.0, hp - amt)
            ev.append(hit(rng.choice([5, 6, 7]), amt, weapon=rng.choice(["gun", "laser", "mortar"])))
        hp = min(100.0, hp + 0.5)
        foes = [enemy(5, (60, 20))] if tick % 5 else []
        msgs.append(obs(tick, (0, 0), foes, hp=hp, z=zone(radius=200, nr=100), events=ev))
    msgs.append({"type": "end", "place": rng.randint(5, 16), "kills": rng.randint(0, 3)})
    return msgs


def play(msgs):
    """Like the server: send one message, wait for the answer (except after `end`)."""
    p = subprocess.Popen([sys.executable, "-u", BOT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines, err = queue.Queue(), []
    threading.Thread(target=lambda: [lines.put(l) for l in p.stdout], daemon=True).start()
    te = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
    te.start()
    answers = 0
    for m in msgs:
        p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
        if m["type"] != "end":
            lines.get(timeout=20)
            answers += 1
    p.stdin.close()
    p.wait(timeout=30)
    te.join(timeout=10)
    return p.returncode, answers, "".join(err)


def main():
    out, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3
    os.makedirs(out, exist_ok=True)
    for i in range(n):
        code, answers, stderr = play(match(i))
        with open(os.path.join(out, f"match{i}.stderr.txt"), "w", encoding="utf-8") as f:
            f.write(stderr)
        print(f"match {i}: exit {code}, answers {answers}, stderr lines {stderr.count(chr(10))}")


if __name__ == "__main__":
    main()
