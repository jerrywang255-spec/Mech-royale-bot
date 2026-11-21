"""E003: the coarse grid LOS must not veto direct fire at an actor the server reports as visible."""
import tempfile
import unittest

from synth import enemy, gun, laser, make_arena, make_bot, obs


class FakeMap:
    """Grid that claims every line is blocked and every direction is open."""

    def los(self, a, b, skip=3.5):
        return False

    def free_dist(self, x, y, yaw, maxd=16.0, start=2.0):
        return maxd


def lined_up(b, o, target):
    """Point the torso exactly where the bot wants to aim, then ask again."""
    yaw, pitch, _ = b._weapons(o, target, "fight")
    o["self"]["aim"] = [yaw, pitch]
    return b._weapons(o, target, "fight")[2]


class ObservedTargetFire(unittest.TestCase):
    def test_grid_false_negative_next_to_wall_still_fires(self):
        # Building edge at y = 38.5 inflates to the 4 m cell edge y = 40, so a clear line along
        # y = 39.5 reads as blocked on the grid.
        with tempfile.TemporaryDirectory() as d:
            make_arena(d, [(0.0, 0.0, 40.0, 38.5, 20.0)], size=256)
            b = make_bot(d)
            self.assertIsNotNone(b.map)
            me, foe = (-10.0, 39.5), (80.0, 39.5)
            self.assertFalse(b.map.los((me[0], me[1], 8.0), (foe[0], foe[1], 4.5)))  # precondition
            e = enemy(5, foe)
            o = obs(10, me, [e])
            self.assertEqual(lined_up(b, o, o["mechs"][0]), [True, False])

    def test_far_observed_target_fires_gun_and_charges_laser(self):
        b = make_bot()
        b.map = FakeMap()
        o = obs(10, (0, 0), [enemy(5, (90, 0))], weapons=[gun(), laser()])
        self.assertEqual(lined_up(b, o, o["mechs"][0]), [True, True])

    def test_near_range_target_still_needs_grid_los(self):
        b = make_bot()                       # synth hello: vision.near_range = 30
        b.map = FakeMap()
        o = obs(10, (0, 0), [enemy(5, (20, 0))], weapons=[gun(), laser()])
        self.assertEqual(lined_up(b, o, o["mechs"][0]), [False, False])

    def test_remembered_actor_not_in_this_obs_needs_grid_los(self):
        b = make_bot()
        b.map = FakeMap()
        o = obs(10, (0, 0), [])
        self.assertFalse(b._has_shot(o, enemy(5, (90, 0))))

    def test_avoid_mode_still_holds_fire(self):
        b = make_bot()
        b.map = FakeMap()
        o = obs(10, (0, 0), [enemy(5, (90, 0))])
        t = o["mechs"][0]
        yaw, pitch, _ = b._weapons(o, t, "avoid")
        o["self"]["aim"] = [yaw, pitch]
        self.assertEqual(b._weapons(o, t, "avoid")[2], [False, False])

    def test_weaker_far_enemy_is_engaged_despite_grid(self):
        b = make_bot()
        b.map = FakeMap()
        foes = [enemy(5, (150, 0), hp=20)]
        o = obs(10, (0, 0), foes)
        self.assertEqual(b._decide(o, o["mechs"], [], 0.9)[1], "fight")


if __name__ == "__main__":
    unittest.main()
