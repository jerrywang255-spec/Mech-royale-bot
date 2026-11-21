"""E004: two or more threats with more combined health than we can trade with -> retreat."""
import math
import unittest

from synth import enemy, hit, make_bot, obs, quiet, zone


def facing(i, pos, me=(0.0, 0.0), hp=100):
    """An enemy whose torso points at `me`."""
    return enemy(i, pos, hp=hp, aim=[math.atan2(me[1] - pos[1], me[0] - pos[0]), 0.0])


def mode_of(b, o, hp_r):
    b._remember(o)
    return b._decide(o, [m for m in o["mechs"] if m["kind"] == "mech"], [], hp_r)[1]


class Outnumbered(unittest.TestCase):
    def test_three_close_enemies_full_hp_retreat(self):
        foes = [facing(5, (50, 0)), facing(6, (0, 55)), facing(7, (-45, 10))]
        self.assertEqual(mode_of(make_bot(), obs(10, (0, 0), foes), 1.0), "retreat")

    def test_two_healthy_enemies_aiming_at_us_retreat(self):
        foes = [facing(5, (80, 0)), facing(6, (0, 90))]
        self.assertEqual(mode_of(make_bot(), obs(10, (0, 0), foes), 1.0), "retreat")

    def test_two_badly_hurt_enemies_are_fought(self):
        foes = [facing(5, (50, 0), hp=25), facing(6, (0, 55), hp=25)]   # combined 0.5 -> need hp > 0.3
        self.assertEqual(mode_of(make_bot(), obs(10, (0, 0), foes), 0.9), "fight")

    def test_single_attacker_unchanged(self):
        foes = [facing(5, (50, 0))]
        self.assertEqual(mode_of(make_bot(), obs(10, (0, 0), foes, events=[hit(5)]), 0.9), "fight")

    def test_final_duel_unchanged(self):
        foes = [facing(5, (50, 0)), facing(6, (0, 55))]
        self.assertEqual(mode_of(make_bot(), obs(10, (0, 0), foes, alive=2), 0.9), "fight")

    def test_enemies_fighting_each_other_are_not_threats(self):
        # far enough that the 60 m reflex doesn't apply; they aim at each other, not at us
        a, b = enemy(5, (100, 0), aim=[math.pi / 2, 0]), enemy(6, (100, 40), aim=[-math.pi / 2, 0])
        bot = make_bot()
        self.assertEqual(bot._threats(obs(10, (0, 0), [a, b])), {})
        self.assertEqual(mode_of(bot, obs(10, (0, 0), [a, b]), 1.0), "avoid")

    def test_hidden_attacker_counts_from_hit_bearing(self):
        bot = make_bot()
        o = obs(10, (0, 0), [facing(5, (80, 0))], events=[hit(9, bearing=math.pi / 2)])
        bot._remember(o)
        th = bot._threats(o)
        self.assertEqual(set(th), {5, 9})
        (hx, hy), s = th[9]
        self.assertAlmostEqual(hx, 0.0, places=6)
        self.assertAlmostEqual(hy, 60.0, places=6)
        self.assertEqual(s, 1.0)

    def test_npc_hits_are_not_mech_threats(self):
        bot = make_bot()
        o = obs(10, (0, 0), [facing(5, (80, 0))], events=[hit(40, weapon="npc", bearing=1.0)])
        bot._remember(o)
        self.assertEqual(set(bot._threats(o)), {5})


class RetreatDirection(unittest.TestCase):
    def test_moves_away_from_both_threats_with_sidestep(self):
        b = make_bot()
        foes = [facing(5, (50, 0)), facing(6, (0, 50))]
        z = zone(radius=400, nr=300)                 # deep inside the next circle: no zone pull
        a, _ = quiet(b.act, obs(10, (0, 0), foes, z=z))
        mx, my = a["move"]
        ax, ay = -math.sqrt(0.5), -math.sqrt(0.5)    # straight away from both
        self.assertGreater(mx * ax + my * ay, 0.8)   # mostly away...
        self.assertGreater(abs(mx * ay - my * ax), 0.3)  # ...but not in a straight line
        self.assertTrue(a["boost"])

    def test_sidestep_changes_side_over_time(self):
        b = make_bot()
        foes = [facing(5, (50, 0)), facing(6, (0, 50))]
        z = zone(radius=400, nr=300)
        ax, ay = -math.sqrt(0.5), -math.sqrt(0.5)
        sides = set()
        for tick in range(10, 200, 3):
            a, _ = quiet(b.act, obs(tick, (0, 0), foes, z=z))
            sides.add(math.copysign(1, a["move"][0] * ay - a["move"][1] * ax))
        self.assertEqual(sides, {-1.0, 1.0})


if __name__ == "__main__":
    unittest.main()
