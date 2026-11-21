"""Break a losing exchange: leave for a reachable spot that no known shooter can see, then come back out."""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from synth import enemy, gun, hit, laser, make_arena, make_bot, obs, quiet, zone

# One building: x -20..20, y 10..22, 20 m tall. The shooter stands far south at (0, -100).
# The bot starts north-west of the building at (-30, 30): the shooter can see it there.
# Spots north of the building with |x| <~ 15 are hidden from the shooter, and the walk east is open.
WALL = (-20.0, 10.0, 20.0, 22.0, 20.0)
ME = (-30.0, 30.0)
FOE = (0.0, -100.0)
WEAPONS_CFG = {"gun": {}, "laser": {}, "lightning": {}, "mortar": {"gravity": 22.0, "splash_radius": 7.0}}
Z = zone(radius=400, nr=300, ss=500.0, se=600.0)    # deep inside a far-off next circle: no zone pressure


def crosses_wall(a, b, box=WALL):
    """Exact 2D test: does segment a->b pass through the box footprint? (Its top is above every sight line.)"""
    x0, y0, x1, y1, _ = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def shooter(**kw):
    aim = math.atan2(ME[1] - FOE[1], ME[0] - FOE[0])
    return enemy(5, FOE, aim=[aim, 0.0], **kw)


class CoverBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A fixed folder under the temp dir (overwritten each run). mkdtemp's owner-only ACL is not
        # writable in some Windows sandboxes, which is why TemporaryDirectory is not used here.
        cls.dir = os.path.join(tempfile.gettempdir(), "mr_test_cover_arena")
        make_arena(cls.dir, [WALL], size=320)
        cls.map = make_bot(cls.dir, weapons=WEAPONS_CFG).map
        assert cls.map is not None

    def bot(self):
        b = make_bot(weapons=WEAPONS_CFG)      # no map dir: reuse the one built once
        b.map = self.map
        return b

    def step(self, b, tick, me=ME, foes=None, events=(), **kw):
        kw.setdefault("z", Z)
        o = obs(tick, me, [shooter()] if foes is None else foes, events=events, **kw)
        a, err = quiet(b.act, o)
        return a, err

    def start(self, b, weapons=None):
        """Two 6%-of-max hits from the shooter, no return fire: a losing exchange."""
        w = weapons or [gun(), None]
        self.step(b, 10, hp=94, events=[hit(5, 6.0, bearing=-math.pi / 2)], weapons=w)
        return self.step(b, 12, hp=88, events=[hit(5, 6.0, bearing=-math.pi / 2)], weapons=w)


class Trigger(CoverBase):
    def test_losing_exchange_moves_to_hidden_reachable_spot(self):
        b = self.bot()
        self.assertTrue(crosses_wall(FOE, (0.0, 30.0)) and not crosses_wall(FOE, ME))
        a, err = self.start(b, weapons=[gun(), laser(charge=0.3)])
        self.assertIsNotNone(b.cover)
        self.assertIn("COVER start", err)
        sx, sy = b.cover["x"], b.cover["y"]
        self.assertTrue(crosses_wall(FOE, (sx, sy)), (sx, sy))          # exact geometry agrees it is hidden
        self.assertFalse(crosses_wall(ME, (sx, sy)), (sx, sy))          # and the walk there is open
        self.assertLessEqual(math.dist(ME, (sx, sy)), 36.0)
        d = math.dist(ME, (sx, sy))
        self.assertAlmostEqual(a["move"][0], (sx - ME[0]) / d, places=3)
        self.assertAlmostEqual(a["move"][1], (sy - ME[1]) / d, places=3)
        self.assertTrue(a["boost"])
        self.assertFalse(a["fire"][1])                                 # no laser charge while relocating

    def test_winning_exchange_keeps_fighting(self):
        b = self.bot()
        self.step(b, 10, hp=94, events=[hit(5, 6.0), {"type": "hit", "target": 5, "amount": 4.0, "weapon": "gun"}])
        self.step(b, 12, hp=88, events=[hit(5, 6.0), {"type": "hit", "target": 5, "amount": 4.0, "weapon": "gun"}])
        self.assertIsNone(b.cover)

    def test_hits_on_crates_are_not_return_fire(self):
        b = self.bot()
        b.props[77] = ("crate", 10.0, 40.0, 0.0, 1.0)
        crate_hit = {"type": "hit", "target": 77, "amount": 50.0, "weapon": "gun"}
        self.step(b, 10, hp=94, events=[hit(5, 6.0), crate_hit])
        self.step(b, 12, hp=88, events=[hit(5, 6.0), crate_hit])
        self.assertIsNotNone(b.cover)

    def test_single_small_hit_or_zone_damage_does_not_trigger(self):
        b = self.bot()
        self.step(b, 10, hp=88, events=[hit(5, 12.0)])                  # one hit: not "repeated"
        self.assertIsNone(b.cover)
        b2 = self.bot()
        zone_hit = {"type": "damaged", "amount": 6.0, "weapon": "zone", "bearing": None, "by": None}
        self.step(b2, 10, hp=94, foes=[], events=[zone_hit])
        self.step(b2, 12, hp=88, foes=[], events=[zone_hit])
        self.assertIsNone(b2.cover)

    def test_keeps_finishing_a_lone_weak_enemy(self):
        b = self.bot()
        weak = [shooter(hp=20)]
        self.step(b, 10, foes=weak, hp=94, events=[hit(5, 6.0)])
        self.step(b, 12, foes=weak, hp=88, events=[hit(5, 6.0)])
        self.assertIsNone(b.cover)

    def test_final_duel_never_hides(self):
        b = self.bot()
        self.step(b, 10, hp=94, events=[hit(5, 6.0)], alive=2)
        self.step(b, 12, hp=88, events=[hit(5, 6.0)], alive=2)
        self.assertIsNone(b.cover)

    def test_no_start_under_zone_pressure(self):
        b = self.bot()
        self.step(b, 10, hp=94, events=[hit(5, 6.0)], in_zone=False)
        self.step(b, 12, hp=88, events=[hit(5, 6.0)], in_zone=False)
        self.assertIsNone(b.cover)

    def test_unseen_attacker_placed_from_hit_bearing(self):
        b = self.bot()
        bearing = math.atan2(FOE[1] - ME[1], FOE[0] - ME[0])
        self.step(b, 10, foes=[], hp=94, events=[hit(5, 6.0, bearing=bearing)])
        self.step(b, 12, foes=[], hp=88, events=[hit(5, 6.0, bearing=bearing)])
        self.assertIsNotNone(b.cover)
        tx, ty, _ = b.cover["threats"][0]
        self.assertAlmostEqual(math.atan2(ty - ME[1], tx - ME[0]), bearing, places=6)

    def test_zone_unsafe_spot_is_rejected(self):
        b = self.bot()
        far = zone(center=(0, 0), radius=400, nc=(130, -130), nr=20, ss=0.5, se=8.0)   # must leave now
        o = obs(12, ME, [shooter()], z=far)
        self.assertFalse(b._cover_zone_ok(o, 0.0, 30.0, 2.0))
        self.assertTrue(b._cover_zone_ok(obs(12, ME, [shooter()], z=Z), 0.0, 30.0, 2.0))


class Episode(CoverBase):
    def test_holds_still_quiet_and_facing_the_shooter_after_arrival(self):
        b = self.bot()
        self.start(b)
        spot = (b.cover["x"], b.cover["y"])
        a, _ = self.step(b, 20, me=spot, foes=[], hp=80)
        self.assertTrue(b.cover["arrived"])
        self.assertEqual(a["move"], [0.0, 0.0])
        self.assertEqual(a["fire"], [False, False])
        self.assertFalse(a["boost"])
        want = math.atan2(FOE[1] - spot[1], FOE[0] - spot[0])
        self.assertAlmostEqual(a["aim"][0], want, places=6)

    def test_healed_exit_waits_for_quiet_time_then_cools_down(self):
        b = self.bot()
        self.start(b)
        spot = (b.cover["x"], b.cover["y"])
        self.step(b, 20, me=spot, foes=[], hp=95)            # 0.8 s after the last hit: regen not running yet
        self.assertIsNotNone(b.cover)
        a, err = self.step(b, 75, me=spot, foes=[], hp=95)   # 6.3 s quiet and >= 90%
        self.assertIsNone(b.cover)
        self.assertIn("reason=healed", err)
        self.assertAlmostEqual(b.cover_next, 7.5 + b.COVER_COOLDOWN_S)
        self.step(b, 76, hp=80, events=[hit(5, 6.0)])
        self.step(b, 78, hp=74, events=[hit(5, 6.0)])
        self.assertIsNone(b.cover)                           # losing again, but inside the cooldown

    def test_zone_pressure_ends_the_episode(self):
        b = self.bot()
        self.start(b)
        a, err = self.step(b, 14, hp=88, in_zone=False)
        self.assertIsNone(b.cover)
        self.assertIn("reason=zone", err)

    def test_timeout(self):
        b = self.bot()
        self.start(b)
        spot = (b.cover["x"], b.cover["y"])
        _, err = self.step(b, 12 + int(b.COVER_MAX_S * 10) + 1, me=spot, foes=[], hp=60)
        self.assertIn("reason=timeout", err)

    def test_hit_while_holding_finds_a_new_spot_or_gives_up_soon(self):
        b = self.bot()
        self.start(b)
        spot = (b.cover["x"], b.cover["y"])
        self.step(b, 20, me=spot, foes=[], hp=80)
        flank = enemy(6, (spot[0], spot[1] + 60), aim=[-math.pi / 2, 0.0])   # comes round the north side
        a, err = self.step(b, 32, me=spot, foes=[flank], hp=74, events=[hit(6, 6.0, bearing=math.pi / 2)])
        if b.cover is not None:
            self.assertEqual(b.cover["repicks"], 1)
            self.assertNotEqual((b.cover["x"], b.cover["y"]), spot)
        else:
            self.assertIn("reason=exposed", err)
            self.assertAlmostEqual(b.cover_next, 3.2 + b.COVER_RETRY_S)

    def test_failed_spot_retries_sooner_than_a_finished_episode(self):
        self.assertLess(self.bot().COVER_RETRY_S, self.bot().COVER_COOLDOWN_S)

    def test_stuck_on_the_way_tries_another_heading_then_gives_up(self):
        b = self.bot()
        self.start(b)
        first = math.atan2(b.cover["y"] - ME[1], b.cover["x"] - ME[0])
        _, err = self.step(b, 23, hp=82)                     # 1.1 s later, still at ME: no progress
        if b.cover is not None:
            self.assertEqual(b.cover["stuck"], 1)
            again = math.atan2(b.cover["y"] - ME[1], b.cover["x"] - ME[0])
            self.assertGreater(abs(math.remainder(again - first, 2 * math.pi)), 0.69)
            _, err = self.step(b, 34, hp=76)                 # stuck again: hand back to normal steering
        self.assertIsNone(b.cover)
        self.assertIn("reason=stuck", err)


class Switch(CoverBase):
    def test_switch_off_never_runs_cover_logic(self):
        b = self.bot()
        b.COVER = False
        b._cover_update = Mock(side_effect=AssertionError("cover logic ran"))
        a, _ = self.start(b)
        self.assertIsNone(b.cover)
        self.assertIn("move", a)

    def test_error_disables_cover_and_the_tick_still_answers(self):
        b = self.bot()
        b._cover_update = Mock(side_effect=ValueError("boom"))
        a, err = self.step(b, 10, hp=94, events=[hit(5, 6.0)])
        self.assertFalse(b.COVER)
        self.assertIn("cover disabled", err)
        self.assertIn("move", a)
        self.assertIn("aim", a)

    def test_no_map_means_no_cover(self):
        b = make_bot(weapons=WEAPONS_CFG)
        self.assertIsNone(b.map)
        self.step(b, 10, hp=94, events=[hit(5, 6.0)])
        self.step(b, 12, hp=88, events=[hit(5, 6.0)])
        self.assertIsNone(b.cover)


class ProtocolSmoke(CoverBase):
    def test_json_lines_round_trip_through_wrapper(self):
        """Lockstep like the server: hello, 60 observations with a losing exchange, end. stdout stays protocol."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hello = {"type": "hello", "protocol": "mechfight/1", "slot": 0, "seed": 3,
                 "map": {"dir": self.dir, "files": []},
                 "config": {"mech": {"step_up": 1.2, "height": 9.0}, "loot": {"pickup_radius": 4.0},
                            "vision": {"range": 250, "fov_deg": 120, "near_range": 30}, "weapons": WEAPONS_CFG}}
        p = subprocess.Popen([sys.executable, "-u", os.path.join(root, "bot.py")], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=root,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            p.stdin.write(json.dumps(hello) + "\n")
            p.stdin.flush()
            ready = json.loads(p.stdout.readline())
            self.assertEqual(ready["type"], "ready")
            hp, me = 100.0, list(ME)
            for tick in range(1, 61):
                ev = []
                if 5 <= tick <= 12 and tick % 2 == 0:
                    hp -= 4.0
                    ev = [hit(5, 4.0, bearing=-math.pi / 2)]
                o = obs(tick, me, [shooter()], hp=hp, z=Z, events=ev)
                p.stdin.write(json.dumps(o) + "\n")
                p.stdin.flush()
                a = json.loads(p.stdout.readline())
                self.assertEqual(a["tick"], tick)
                self.assertLessEqual(math.hypot(*a["move"]), 1.0 + 1e-9)
                me = [me[0] + a["move"][0] * 1.17, me[1] + a["move"][1] * 1.17]
            p.stdin.write(json.dumps({"type": "end", "place": 9, "kills": 0}) + "\n")
            p.stdin.flush()
            out, err = p.communicate(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(out.strip(), "")                      # nothing but the answers we already read
        self.assertIn("COVER start", err)
        self.assertIn("LAST t=6.0", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
