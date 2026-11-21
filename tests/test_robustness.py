"""E001: malformed or partial observations must not freeze the bot."""
import math
import unittest

from synth import B, enemy, gun, hello, laser, make_bot, obs, quiet, zone


class OptionalFields(unittest.TestCase):
    def test_enemy_without_hp_fields_still_targeted(self):
        b = make_bot()
        e = {k: v for k, v in enemy(5, (50, 0)).items() if k not in ("hp", "max_hp")}
        a, err = quiet(b.act, obs(10, (0, 0), [e]))
        self.assertEqual(err, "")
        self.assertEqual(b.errors, 0)
        self.assertIn("fire", a)
        self.assertEqual(B.hp_frac(e), 1.0)

    def test_enemy_hp_fraction(self):
        self.assertAlmostEqual(B.hp_frac({"hp": 25, "max_hp": 100}), 0.25)
        self.assertEqual(B.hp_frac({"hp": 25}), 1.0)
        self.assertEqual(B.hp_frac({"hp": 25, "max_hp": 0}), 1.0)

    def test_missing_lists_and_offer_choices(self):
        b = make_bot()
        o = obs(10, (0, 0), [enemy(5, (80, 0))])
        for k in ("projectiles", "beams", "drops", "crates", "cubes", "pickups", "sounds", "events"):
            del o[k]
        o["offer"] = {"id": 1, "expires": 5}
        a, err = quiet(b.act, o)
        self.assertEqual((b.errors, err), (0, ""))
        self.assertNotIn("pick", a)

    def test_malformed_entries_are_dropped(self):
        b = make_bot()
        o = obs(10, (0, 0), [enemy(5, (80, 0))])
        o["mechs"] += [{"id": 9, "kind": "mech"}, "junk"]
        o["sounds"] = [{"kind": "gun"}, {"kind": "gun", "bearing": 1.0, "dist": "near", "age": 0}]
        o["pickups"] = [{"id": 3, "pos": [1, 1, 0]}]
        o["events"] = [{"type": "take_failed"}, {"amount": 3}]
        a, err = quiet(b.act, o)
        self.assertEqual((b.errors, err), (0, ""))
        self.assertEqual([m["id"] for m in o["mechs"]], [5])

    def test_short_or_empty_weapon_lists(self):
        for weapons in ([], [gun()], [None, laser()], [{"kind": "rail"}, gun()]):
            b = make_bot()
            a, err = quiet(b.act, obs(10, (0, 0), [enemy(5, (40, 0))], weapons=weapons))
            self.assertEqual((b.errors, err), (0, ""), weapons)
            self.assertEqual(len(a["fire"]), 2)

    def test_hello_without_config(self):
        b, _ = quiet(B.Bot, {"type": "hello", "seed": 3})
        self.assertIsNone(b.map)
        a, err = quiet(b.act, obs(10, (0, 0)))
        self.assertEqual((b.errors, err), (0, ""))
        self.assertIn("move", a)


class Fallback(unittest.TestCase):
    def _broken(self):
        b = make_bot()

        def boom(*a, **k):
            raise RuntimeError("synthetic failure")
        b._decide = boom
        return b

    def test_exception_falls_back_toward_zone_when_outside(self):
        b = self._broken()
        o = obs(10, (300, 0), [enemy(5, (320, 0))], z=zone(radius=200, nr=100), in_zone=False)
        a, err = quiet(b.act, o)
        self.assertEqual(b.errors, 1)
        self.assertIn("synthetic failure", err)
        self.assertLess(a["move"][0], -0.99)       # straight back toward (0, 0)
        self.assertTrue(a["boost"])
        self.assertEqual(len(a["aim"]), 2)

    def test_exception_holds_still_deep_inside_next_circle(self):
        b = self._broken()
        a, _ = quiet(b.act, obs(10, (5, 5), z=zone(radius=200, nr=100)))
        self.assertEqual(a["move"], [0.0, 0.0])
        self.assertFalse(a["boost"])

    def test_error_log_is_rate_limited(self):
        b = self._broken()
        logs = [quiet(b.act, obs(i, (0, 0)))[1] for i in range(1, 50)]
        self.assertEqual(sum(1 for s in logs if s), 5)

    def test_missing_self_returns_empty_action(self):
        b = make_bot()
        a, _ = quiet(b.act, {"type": "obs", "tick": 1})
        self.assertEqual(a, {})


if __name__ == "__main__":
    unittest.main()
