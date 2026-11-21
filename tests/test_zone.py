"""E006: camping next to a wall must not take us out of a small next circle."""
import tempfile
import unittest

from synth import make_arena, make_bot, obs, quiet, zone

# Next circle: center (0, 0), radius 30 (camp spots must be within 24 m). Bot at (10, 0).
# East wall foot at x = 26 (16 m away, 26 m from center: outside). North wall foot at y = 18
# (18 m away, 20.6 m from center: inside).
EAST = (30.0, -40.0, 60.0, 40.0, 15.0)
NORTH = (-10.0, 20.0, 26.0, 40.0, 15.0)


class CampInsideNextCircle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        make_arena(self.tmp.name, [EAST, NORTH], size=256)
        self.z = zone(center=(0, 0), radius=60, nc=(0, 0), nr=30, ss=60.0, se=120.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_wall_inside_the_circle(self):
        b = make_bot(self.tmp.name)
        o = obs(10, (10, 0), z=self.z)
        self.assertEqual(b._urgency(o), 0.0)
        cx, cy = b._camp(o)
        self.assertAlmostEqual(cx, 0.0, places=6)
        self.assertGreater(cy, 0.4)                # north, not the closer east wall

    def test_no_wall_inside_means_stay_put(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            make_arena(tmp.name, [EAST], size=256)
            b = make_bot(tmp.name)
            self.assertEqual(b._camp(obs(10, (10, 0), z=self.z)), (0.0, 0.0))
        finally:
            tmp.cleanup()

    def test_large_circle_keeps_nearest_wall(self):
        b = make_bot(self.tmp.name)
        big = zone(center=(0, 0), radius=600, nc=(0, 0), nr=300)
        cx, cy = b._camp(obs(10, (10, 0), z=big))
        self.assertGreater(cx, 0.4)                # east wall (nearest) is fine in a big circle
        self.assertAlmostEqual(cy, 0.0, places=6)

    def test_act_does_not_walk_out(self):
        b = make_bot(self.tmp.name)
        a, _ = quiet(b.act, obs(10, (10, 0), z=self.z))
        self.assertLessEqual(a["move"][0], 0.05)


if __name__ == "__main__":
    unittest.main()
