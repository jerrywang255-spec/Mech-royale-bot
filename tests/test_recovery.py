"""E005: once low, keep disengaging (no loot, no optional fights) until health is back."""
import math
import unittest

from synth import enemy, hit, make_bot, obs, quiet, zone

Z = zone(radius=400, nr=300)        # deep inside the next circle: zone pull is zero


def step(b, tick, hp, foes=(), events=(), alive=16, **kw):
    o = obs(tick, (0, 0), foes, hp=hp, z=Z, events=events, alive=alive, **kw)
    a, _ = quiet(b.act, o)
    return a, o


class Latch(unittest.TestCase):
    def test_stays_in_retreat_while_regenerating(self):
        b = make_bot()
        foe = enemy(5, (50, 0))
        step(b, 10, 30, [foe], [hit(5)])
        self.assertTrue(b.recovering)
        for hp in (40, 55, 69):      # baseline would fight at >= 35% with an enemy inside 60 m
            step(b, 100 + hp, hp, [foe])
            self.assertEqual(b._decide(*self._d(b, hp, [foe]))[1], "retreat", hp)
        step(b, 200, 75, [foe])
        self.assertFalse(b.recovering)
        self.assertEqual(b._decide(*self._d(b, 75, [foe]))[1], "fight")

    def _d(self, b, hp, foes):
        o = obs(300, (0, 0), foes, hp=hp, z=Z)
        return o, o["mechs"], [], hp / 100

    def test_no_latch_in_final_duel(self):
        b = make_bot()
        step(b, 10, 20, [enemy(5, (50, 0))], [hit(5)], alive=2)
        self.assertFalse(b.recovering)

    def test_finishes_a_lone_weaker_enemy(self):
        b = make_bot()
        step(b, 10, 30, [enemy(5, (50, 0), hp=10)], [hit(5)])
        self.assertTrue(b.recovering)
        o = obs(11, (0, 0), [enemy(5, (50, 0), hp=10)], hp=30, z=Z)
        b._remember(o)
        self.assertEqual(b._decide(o, o["mechs"], [], 0.30)[1], "fight")

    def test_weaker_enemy_with_company_is_not_chased(self):
        b = make_bot()
        foes = [enemy(5, (50, 0), hp=10), enemy(6, (0, 50), aim=[-math.pi / 2, 0])]
        step(b, 10, 30, foes, [hit(5)])
        o = obs(11, (0, 0), foes, hp=30, z=Z)
        b._remember(o)
        self.assertEqual(b._decide(o, o["mechs"], [], 0.30)[1], "retreat")


class QuietWhileRecovering(unittest.TestCase):
    def test_ignores_loot_and_crates(self):
        b = make_bot()
        step(b, 10, 30, [enemy(5, (50, 0))], [hit(5)])
        o = obs(200, (0, 0), [], hp=50, z=Z)
        o["pickups"] = [{"id": 70, "kind": "laser", "pos": [30.0, 0.0, 0.0]}]
        o["cubes"] = [{"id": 71, "pos": [0.0, 20.0, 0.0]}]
        o["crates"] = [{"id": 72, "pos": [40.0, 0.0, 0.0], "hp": 10}]
        a, _ = quiet(b.act, o)
        self.assertEqual(a["move"], [0.0, 0.0])
        self.assertEqual(a["fire"], [False, False])
        b2 = make_bot()                                 # same scene, healthy: goes for the laser
        a2, _ = quiet(b2.act, o)
        self.assertGreater(a2["move"][0], 0.9)

    def test_moves_away_from_unseen_attacker(self):
        b = make_bot()
        step(b, 10, 30, [enemy(5, (50, 0))], [hit(5, bearing=0.0)])
        a, _ = step(b, 20, 32, [], [hit(5, bearing=0.0)])   # shot from the east, nobody visible
        self.assertLess(a["move"][0], -0.9)
        self.assertTrue(a["boost"])


if __name__ == "__main__":
    unittest.main()
