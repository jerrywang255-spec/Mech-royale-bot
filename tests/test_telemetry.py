"""E002: stderr telemetry and an end-to-end JSON-lines protocol smoke test."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from synth import B, HERE, enemy, hit, make_arena, make_bot, obs, quiet, zone

BOT_DIR = os.path.dirname(HERE)


class TelemetryCounters(unittest.TestCase):
    def test_counts_damage_kills_zone_time_and_modes(self):
        b = make_bot()
        quiet(b.act, obs(1, (0, 0), [enemy(5, (50, 0))], events=[hit(5, 12, weapon="gun")]))
        o = obs(2, (0, 0), [enemy(5, (50, 0))], in_zone=False,
                events=[hit(6, 8, weapon="laser"), {"type": "hit", "target": 5, "amount": 20, "weapon": "gun"},
                        {"type": "kill", "target": 5}, {"type": "damaged", "amount": 4, "weapon": "zone",
                                                        "bearing": None, "by": None}])
        quiet(b.act, o)
        quiet(b.act, obs(4, (0, 0), in_zone=False))       # 2 ticks later: gap, 0.2 s outside
        s = b.tm.snapshot(final=True)
        self.assertEqual(s["taken"], {"gun": 12, "laser": 8, "zone": 4})
        self.assertEqual(s["dealt"], {"gun": 20})
        self.assertEqual(s["kills"], [0.2])
        self.assertAlmostEqual(s["out_zone_s"], 0.3, places=5)
        self.assertEqual(s["gaps"], 1)
        self.assertEqual(s["ticks"], 3)
        self.assertEqual(s["death_window"]["attackers"], 2)
        self.assertEqual(sum(s["modes"].values()), 3)
        self.assertGreaterEqual(s["lat_ms"]["p99"], 1)

    def test_periodic_line_once_per_period(self):
        b = make_bot()
        out = "".join(quiet(b.act, obs(tick, (0, 0)))[1] for tick in range(590, 1300, 7))
        self.assertEqual(out.count("TELEMETRY "), 2)      # t = 60 s and t = 120 s
        line = [l for l in out.splitlines() if l.startswith("TELEMETRY ")][0]
        json.loads(line[len("TELEMETRY "):])

    def test_final_summary_once_and_tap_records_end(self):
        b = make_bot()
        quiet(b.act, obs(1, (0, 0)))
        lines = list(B.StdinTap(['{"type":"obs","tick":2}\n', '{"type":"end","place":7,"kills":2}\n']))
        self.assertEqual(len(lines), 2)
        _, err = quiet(B._final_summary)
        _, err2 = quiet(B._final_summary)
        self.assertEqual(err2, "")
        summary = json.loads(err.strip().split("SUMMARY ", 1)[1])
        self.assertEqual(summary["end"], {"place": 7, "kills": 2})

    def test_end_seen_before_bot_exists_is_kept(self):
        B._TELEMETRY.clear()
        B._END.clear()
        list(B.StdinTap(['{"type":"end","place":3,"kills":4}\n']))   # reader ran ahead of hello
        b = make_bot()
        quiet(b.act, obs(1, (0, 0)))
        _, err = quiet(B._final_summary)
        self.assertEqual(json.loads(err.strip().split("SUMMARY ", 1)[1])["end"], {"place": 3, "kills": 4})
        B._END.clear()

    def test_fallback_counted(self):
        b = make_bot()
        b._decide = lambda *a: 1 / 0
        quiet(b.act, obs(1, (0, 0)))
        self.assertEqual(b.tm.fallbacks, 1)


class ProtocolSmoke(unittest.TestCase):
    """Run bot.py as the scorer would: JSON lines on stdin/stdout, diagnostics on stderr only."""

    def test_json_lines_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            make_arena(d, [(20, 20, 50, 40, 12.0), (-60, -30, -30, 10, 18.0)], size=256)
            hello = {"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": 4,
                     "map": {"dir": d, "files": []},
                     "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0}}}
            msgs = [hello]
            for tick in range(1, 41):
                o = obs(tick, (0, 0), [enemy(5, (60, 10))] if tick % 4 else [], z=zone(radius=120, nr=60),
                        events=[hit(5)] if tick % 10 == 0 else [])
                o["offer"] = {"id": 1, "expires": 9, "choices": [{"id": "armor", "name": "", "desc": ""}]} \
                    if tick == 20 else None
                msgs.append(o)
            msgs.append({"type": "end", "place": 9, "kills": 1})
            p = subprocess.Popen([sys.executable, "-u", os.path.join(BOT_DIR, "bot.py")], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=BOT_DIR,
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(l) for l in p.stdout], daemon=True).start()
            out = []
            try:
                for m in msgs:   # like the server: one message, then wait for the answer
                    p.stdin.write(json.dumps(m) + "\n")
                    p.stdin.flush()
                    if m["type"] != "end":
                        out.append(json.loads(lines.get(timeout=20)))
                p.stdin.close()
                p.wait(timeout=20)
                err = p.stderr.read()
            finally:
                if p.poll() is None:
                    p.kill()
                p.stdout.close()
                p.stderr.close()
        self.assertEqual(p.returncode, 0, err)
        self.assertEqual(out[0], {"type": "ready", "name": "survivor"})
        actions = out[1:]
        self.assertEqual([a["tick"] for a in actions], list(range(1, 41)))
        self.assertTrue(all(len(a.get("fire", [])) == 2 and math_ok(a) for a in actions))
        self.assertEqual(actions[19].get("pick"), 0)
        self.assertNotIn("Traceback", err)
        summary = [l for l in err.splitlines() if l.startswith("SUMMARY ")]
        self.assertEqual(len(summary), 1, err)
        s = json.loads(summary[0][8:])
        self.assertEqual(s["end"], {"place": 9, "kills": 1})
        self.assertEqual(s["ticks"], 40)
        self.assertIn("CONFIG ", err)


def math_ok(a):
    mv = a.get("move", [0, 0])
    return len(mv) == 2 and (mv[0] ** 2 + mv[1] ** 2) <= 1.0 + 1e-6 and len(a.get("aim", [0, 0])) == 2


if __name__ == "__main__":
    unittest.main()
