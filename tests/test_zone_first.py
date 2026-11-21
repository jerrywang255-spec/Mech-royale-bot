"""A mortar-shell dodge must not walk us back toward the closing circle while the zone needs us (experiment 2)."""
import math
import unittest

from synth import enemy, hit, make_bot, obs, quiet, zone

G = 22.0
CFG = {"mortar": {"gravity": G, "splash_radius": 7.0}, "gun": {}, "laser": {}, "lightning": {}}
# We are outside a circle that has nearly closed on us; safety is the next circle 200 m to the east.
OUT = zone(center=(0, 0), radius=20, nc=(200, 0), nr=60, ss=0.0, se=30.0)


def shell(land, t_land, height=30.0):
    vz = (0.5 * G * t_land ** 2 - height) / t_land            # lands at ground level at `land` in `t_land` s
    return {"id": 900, "kind": "mortar", "pos": [land[0], land[1], height], "vel": [0.0, 0.0, vz]}


def decide(b, projectiles=(), foes=(), events=(), z=OUT, in_zone=False, hp=70, me=(0, 0)):
    o = obs(100, me, list(foes), hp=hp, z=z, in_zone=in_zone, events=list(events))
    o["projectiles"] = list(projectiles)
    a, _ = quiet(b.act, o)
    return a, o


class ZoneFirst(unittest.TestCase):
    def test_shell_ahead_does_not_send_us_back_toward_the_edge(self):
        a, _ = decide(make_bot(weapons=CFG), [shell((4.0, 0.0), 0.8)])      # blast lands on the way out
        self.assertGreater(a["move"][0], 0.2)                               # still heading east
        self.assertGreater(abs(a["move"][1]), 0.3)                          # and sidestepping the blast

    def test_old_arbitration_walked_away_from_safety(self):
        b = make_bot(weapons=CFG)
        b.ZONE_FIRST = False
        a, _ = decide(b, [shell((4.0, 0.0), 0.8)])
        self.assertLess(a["move"][0], 0.0)                                   # documents the defect the switch fixes

    def test_shell_behind_us_still_pushes_us_forward_and_boosts(self):
        a, _ = decide(make_bot(weapons=CFG), [shell((-4.0, 0.0), 0.8)])
        self.assertGreater(a["move"][0], 0.9)
        self.assertTrue(a["boost"])

    def test_shell_to_the_side_is_dodged_sideways(self):
        a, _ = decide(make_bot(weapons=CFG), [shell((0.0, 4.0), 0.8)])
        self.assertLess(a["move"][1], -0.3)                                  # away from the blast, zone pull intact
        self.assertGreater(a["move"][0], 0.2)

    def test_helper_replaces_a_backward_dodge_with_a_perpendicular_one(self):
        b = make_bot(weapons=CFG)
        o = obs(100, (0, 0), [], z=OUT, in_zone=False)
        v = b._dodge_toward_safety(o, (-1.0, 0.0))                           # straight back toward the edge
        self.assertAlmostEqual(math.hypot(*v), 1.0, places=6)
        self.assertAlmostEqual(v[0], 0.0, places=6)
        self.assertGreater(abs(v[1]), 0.99)
        w = b._dodge_toward_safety(o, (-0.6, 0.8))                           # partly backward: keep the sideways part
        self.assertAlmostEqual(w[0], 0.0, places=6)
        self.assertAlmostEqual(w[1], 1.0, places=6)
        self.assertEqual(b._dodge_toward_safety(o, (0.5, 0.5)), (0.5, 0.5))  # forward dodges pass through untouched

    def test_no_change_when_the_zone_is_not_pressing(self):
        calm = zone(radius=400, nr=300, ss=500.0, se=600.0)
        o_args = dict(z=calm, in_zone=True)
        a_new, _ = decide(make_bot(weapons=CFG), [shell((4.0, 0.0), 0.8)], **o_args)
        old = make_bot(weapons=CFG)
        old.ZONE_FIRST = False
        a_old, _ = decide(old, [shell((4.0, 0.0), 0.8)], **o_args)
        self.assertLess(a_new["move"][0], 0.0)                               # dodges west, away from the blast
        # with the zone calm the only difference allowed is the removed double weight
        self.assertLess(a_old["move"][0], 0.0)

    def test_no_shell_means_identical_actions(self):
        for foes, events in (([], []), ([enemy(5, (60, 0), aim=[math.pi, 0.0])], [hit(5, 10.0, bearing=0.0)])):
            a_new, _ = decide(make_bot(weapons=CFG), foes=foes, events=events)
            old = make_bot(weapons=CFG)
            old.ZONE_FIRST = False
            a_old, _ = decide(old, foes=foes, events=events)
            for k in ("move", "boost", "aim", "fire"):
                self.assertEqual(a_new[k], a_old[k], k)


class DefensiveConfig(unittest.TestCase):
    def test_missing_weapons_config_does_not_raise(self):
        b = make_bot()                                                       # synth hello has no `weapons`
        o = obs(10, (0, 0), [], z=zone(radius=400, nr=300))
        o["projectiles"] = [shell((3.0, 0.0), 0.8)]
        self.assertIsNotNone(b._shell_dodge(o))                              # default gravity / splash used
        a, err = quiet(b.act, o)
        self.assertNotIn("act() failed", err)
        self.assertIn("move", a)


if __name__ == "__main__":
    unittest.main()
