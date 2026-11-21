"""SITREP: decision-neutral counters for the five reviewed situations (heavy loss, zone margin, pursuit,
muzzle lane, drops). The main property is that switching it off changes no action."""
import json
import math
import os
import tempfile
import unittest

import bot as B
from synth import enemy, gun, hit, laser, make_arena, make_bot, obs, quiet, zone

WEAPONS_CFG = {"gun": {}, "laser": {}, "lightning": {}, "mortar": {"gravity": 22.0, "splash_radius": 7.0}}
Z = zone(radius=400, nr=300, ss=500.0, se=600.0)     # far from closing: no zone pressure


def fresh(**kw):
    b = make_bot(weapons=WEAPONS_CFG)
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def script():
    """40 ticks: an enemy walks in, shoots us, we take hits, a zone phase starts closing."""
    out = []
    for i in range(1, 41):
        foes = [enemy(5, (120 - 2 * i, 10), aim=[math.pi, 0.0], vel=[-8.0, 0, 0])] if i > 3 else []
        ev = [hit(5, 30.0, bearing=0.0)] if 10 <= i < 26 and i % 2 == 0 else []
        z = zone(radius=300, nr=60, nc=(150, 0), ss=2.0, se=40.0) if i > 20 else Z
        out.append(obs(i, (0, 0), foes, hp=max(10, 100 - 3 * i), z=z, events=ev, weapons=[gun(), laser()]))
    return out


class DecisionNeutral(unittest.TestCase):
    def test_actions_identical_with_sitrep_off(self):
        a, b = fresh(), fresh(SITREP=False)
        for o in script():
            ra, _ = quiet(a.act, json.loads(json.dumps(o)))
            rb, _ = quiet(b.act, json.loads(json.dumps(o)))
            for k in ("move", "boost", "jump", "aim", "fire", "reload", "take", "drop", "pick"):
                self.assertEqual(ra.get(k), rb.get(k), (o["tick"], k))
        self.assertGreater(a.sit["mech_taken"], 0)          # it did count something
        self.assertEqual(b.sit["mech_taken"], 0)            # and the off switch really skipped it

    def test_error_disables_only_telemetry(self):
        b = fresh()
        b._is_prop = None                                    # break something _sitrep calls on a hit event
        o = obs(10, (0, 0), [], events=[{"type": "hit", "target": 5, "amount": 5.0, "weapon": "gun"}])
        a, err = quiet(b.act, o)
        self.assertFalse(b.SITREP)
        self.assertIn("sitrep disabled", err)
        self.assertIn("move", a)

    def test_line_is_json_without_private_keys(self):
        b = fresh()
        for o in script()[:12]:
            quiet(b.act, o)
        line = B._sit_line(b.sit)
        self.assertTrue(line.startswith("SITREP {"))
        d = json.loads(line[len("SITREP "):])
        self.assertIn("heavy", d)
        self.assertFalse([k for k in d if k.startswith("_")])


class Counters(unittest.TestCase):
    def test_heavy_loss_and_engagement_hp(self):
        b = fresh()
        quiet(b.act, obs(10, (0, 0), [enemy(5, (100, 0))], hp=100, z=Z))
        self.assertEqual(b.sit["engage_hp"], [1.0])          # first sighting: full armor
        for tick, hp in ((12, 90), (14, 80)):                 # 10% + 10% + 10% of max HP inside 3 s
            quiet(b.act, obs(tick, (0, 0), [enemy(5, (100, 0))], hp=hp, z=Z, events=[hit(5, 10.0)]))
        self.assertEqual(b.sit["heavy"], 0)
        quiet(b.act, obs(16, (0, 0), [enemy(5, (100, 0))], hp=60, z=Z, events=[hit(5, 10.0)]))   # 30% in 3 s
        self.assertEqual(b.sit["heavy"], 1)
        self.assertEqual(b.sit["heavy_ts"], [1.6])
        quiet(b.act, obs(17, (0, 0), [enemy(5, (100, 0))], hp=55, z=Z, events=[hit(5, 5.0)]))
        self.assertEqual(b.sit["heavy"], 1)                   # still the same episode
        # 8 s with nobody in view, then a new engagement at 40% armor
        quiet(b.act, obs(100, (0, 0), [enemy(6, (100, 0))], hp=40, z=Z))
        self.assertEqual(b.sit["engage_hp"], [1.0, 0.4])

    def test_zone_events_and_npc_damage_are_separate(self):
        b = fresh()
        zone_hit = {"type": "damaged", "amount": 12.0, "weapon": "zone", "bearing": None, "by": None}
        npc_hit = {"type": "damaged", "amount": 7.0, "weapon": "npc", "bearing": 0.0, "by": 900}
        quiet(b.act, obs(10, (0, 0), [], z=Z, events=[zone_hit, npc_hit], in_zone=False))
        self.assertEqual(b.sit["zone_taken"], 12.0)
        self.assertEqual(b.sit["mech_taken"], 0.0)
        self.assertGreater(b.sit["out_zone_s"], 0.0)

    def test_thin_zone_margin_idle_and_late(self):
        b = fresh()
        z = zone(radius=100, nr=30, nc=(150, 0), ss=0.0, se=20.0)   # at t=10 the circle is already past us
        o = obs(100, (0, 0), [], z=z)
        quiet(b.act, o)
        self.assertLess(b.sit["min_margin"], 0)
        self.assertGreater(b.sit["thin_s"], 0)
        self.assertGreater(b.sit["hold_thin_s"], 0)           # standing still while the margin is gone
        # ample time: next circle far in the future, we are well inside, margin undefined or large
        b2 = fresh()
        quiet(b2.act, obs(100, (0, 0), [], z=Z))
        self.assertEqual(b2.sit["thin_s"], 0)

    def test_pursuit_needs_walking_at_a_target_that_we_cannot_hit(self):
        b = fresh()
        tgt = enemy(5, (100, 0))
        o = obs(50, (0, 0), [tgt], z=Z)
        o["self"]["vel"] = [8.0, 0, 0]
        b._sitrep(o, "fight", tgt, [False, False], "fight", 1.0)
        self.assertGreater(b.sit["pursuit_s"], 0)
        before = b.sit["pursuit_s"]
        b.sit["_hit_t"] = o["t"] - 1.0                         # we landed a hit 1 s ago: it is not futile
        b._sitrep(o, "fight", tgt, [False, False], "fight", 1.0)
        self.assertEqual(b.sit["pursuit_s"], before)
        o2 = obs(51, (0, 0), [tgt], z=Z)
        o2["self"]["vel"] = [-8.0, 0, 0]                       # walking away is not pursuit
        b.sit["_hit_t"] = -99.0
        b._sitrep(o2, "fight", tgt, [False, False], "fight", 1.0)
        self.assertEqual(b.sit["pursuit_s"], before)

    def test_drop_detour_capture_and_abandon(self):
        b = fresh()
        o = obs(10, (0, 0), [], hp=60, z=Z)
        b._sitrep(o, "None", None, [False, False], "-> drop", 0.6)
        o = obs(40, (0, 0), [], hp=60, z=Z, events=[{"type": "drop", "id": 1, "cubes": 3}])
        b._sitrep(o, "None", None, [False, False], "-> drop", 0.9)
        self.assertEqual(b.sit["drop_captured"], 1)
        self.assertEqual(b.sit["drop_hp"], [[0.6, 0.9, 3.0]])
        b._sitrep(obs(50, (0, 0), [], z=Z), "None", None, [False, False], "-> drop", 0.9)
        b._sitrep(obs(90, (0, 0), [], z=Z), "None", None, [False, False], "None", 0.9)   # gave up for > 3 s
        self.assertEqual(b.sit["drop_abandoned"], 1)


class Lane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(tempfile.gettempdir(), "mr_test_sitrep_arena")
        # a 20 m wall 0.5-2.9 m in front of the shooter: inside the bot's 3 m sight-check skip distance
        make_arena(cls.dir, [(0.5, -40.0, 2.9, 40.0, 20.0)], size=320)

    def test_wall_at_the_muzzle_is_counted_even_though_the_bot_skips_it(self):
        b = make_bot(self.dir, weapons=WEAPONS_CFG)
        self.assertIsNotNone(b.map)
        tgt = enemy(5, (60, 0))
        o = obs(50, (0, 0), [tgt], z=Z, weapons=[gun(), None])
        self.assertTrue(b._can_see(o["self"], tgt))            # the existing gate lets the shot through
        b._sitrep(o, "fight", tgt, [True, False], "fight", 1.0)
        lane = b.sit["lane_blocked"]
        self.assertEqual(lane["n"], 1)
        self.assertEqual(lane.get("muzzle", 0), 0)             # same 3 m skip as the gate: looks clear
        self.assertEqual(lane["muzzle_near"], 1)               # 0.5 m skip: the wall is right there
        self.assertEqual(b.sit["fire_ticks"], {"gun": 1})


if __name__ == "__main__":
    unittest.main()
