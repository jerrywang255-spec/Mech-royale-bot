import math
import unittest
from unittest.mock import Mock

from synth import make_bot, obs


class Navigation(unittest.TestCase):
    def test_loot_detours_require_health_and_zone_margin(self):
        bot = make_bot()
        observation = obs(30, (0, 0))
        observation["pickups"] = [{"id": 1, "pos": [10, 0, 0], "kind": "laser"}]
        bot._urgency = Mock(return_value=0.0)
        self.assertIsNotNone(bot._pick_goal(observation, 1.0))
        self.assertIsNone(bot._pick_goal(observation, 0.39))
        bot._urgency.return_value = 0.41
        self.assertIsNone(bot._pick_goal(observation, 1.0))

    def test_recovering_bot_can_seek_safe_drop(self):
        bot = make_bot()
        observation = obs(30, (0, 0), hp=30)
        observation["drops"] = [{"id": 1, "pos": [10, 0, 0], "lands_in": 0}]
        self.assertEqual(bot._pick_goal(observation, 0.3, drops_only=True)[3], "drop")
        bot.attackers[5] = observation["t"]
        self.assertIsNone(bot._pick_goal(observation, 0.3, drops_only=True))

    def test_stuck_recovery_uses_open_side_without_boost(self):
        bot = make_bot()
        bot.map = Mock()
        bot.map.free_dist.side_effect = (
            lambda x, y, yaw, limit: limit if abs(yaw - math.pi / 2) < 0.1 else 2.0
        )
        bot.track_pos, bot.track_t = (0, 0), 2.0
        move, jump, boost = bot._steer(obs(30, (0, 0)), (1, 0), "avoid")
        self.assertAlmostEqual(move[1], 1.0)
        self.assertFalse(jump)
        self.assertFalse(boost)
