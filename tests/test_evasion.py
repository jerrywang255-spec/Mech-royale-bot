"""E007: dodge predicted mortar impacts and any charging laser aimed at us."""
import math
import unittest

from synth import enemy, make_bot, obs, quiet, zone

Z = zone(radius=400, nr=300)        # deep inside the next circle: zone pull is zero
G = 22.0


def shell(i, land, t_land, height=30.0, speed_xy=(20.0, 0.0)):
    """A mortar shell that will hit the ground (z = 0) at `land` in `t_land` seconds."""
    vx, vy = speed_xy
    px, py = land[0] - vx * t_land, land[1] - vy * t_land
    vz = (0.5 * G * t_land ** 2 - height) / t_land        # height + vz t - g t^2 / 2 = 0
    return {"id": i, "kind": "mortar", "pos": [px, py, height], "vel": [vx, vy, vz]}


class MortarDodge(unittest.TestCase):
    def test_predicts_impact_and_time(self):
        b = make_bot()
        o = obs(10, (0, 0), z=Z)
        o["projectiles"] = [shell(1, (3.0, 0.0), 0.8)]
        (dx, dy), tl = b._shell_dodge(o)
        self.assertAlmostEqual(tl, 0.8, places=6)
        self.assertAlmostEqual(dx, -1.0, places=6)          # impact is east of us: go west
        self.assertAlmostEqual(dy, 0.0, places=6)

    def test_act_moves_out_and_boosts(self):
        b = make_bot()
        o = obs(10, (0, 0), z=Z)
        o["projectiles"] = [shell(1, (0.0, 4.0), 0.8)]
        a, _ = quiet(b.act, o)
        self.assertLess(a["move"][1], -0.9)
        self.assertTrue(a["boost"])

    def test_ignores_far_late_and_gun_rounds(self):
        b = make_bot()
        o = obs(10, (0, 0), z=Z)
        o["projectiles"] = [shell(1, (40.0, 0.0), 0.8), shell(2, (2.0, 0.0), 4.0),
                            {"id": 3, "kind": "gun", "pos": [10.0, 0.0, 7.0], "vel": [-250.0, 0.0, 0.0]}]
        self.assertIsNone(b._shell_dodge(o))
        a, _ = quiet(b.act, o)
        self.assertEqual(a["move"], [0.0, 0.0])

    def test_splash_radius_from_config(self):
        b = make_bot(weapons={"mortar": {"gravity": G, "splash": 12.0}})
        o = obs(10, (0, 0), z=Z)
        o["projectiles"] = [shell(1, (13.0, 0.0), 1.0)]   # outside 7 + 3, inside 12 + 3
        self.assertIsNotNone(b._shell_dodge(o))
        self.assertIsNone(make_bot()._shell_dodge(o))


class LaserTelegraph(unittest.TestCase):
    def test_sidesteps_laser_from_non_target(self):
        b = make_bot()
        near = enemy(5, (50, 0))                                     # closest: becomes the target
        sniper = enemy(6, (0, 200), charging=True, aim=[-math.pi / 2, 0.0])   # aims straight at us
        a, _ = quiet(b.act, obs(10, (0, 0), [near, sniper], z=Z))
        self.assertGreater(abs(a["move"][0]), 0.6)                    # sideways to the sniper's line
        self.assertTrue(a["boost"])

    def test_no_sidestep_when_laser_points_elsewhere(self):
        b = make_bot()
        sniper = enemy(6, (0, 200), charging=True, aim=[0.0, 0.0])
        self.assertFalse(b._laser_on_me(obs(10, (0, 0), [sniper], z=Z), sniper))


if __name__ == "__main__":
    unittest.main()
