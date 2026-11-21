"""Compare the optimized zone field with a full multi-source search."""
import heapq
import math
import unittest
from types import SimpleNamespace

import numpy as np
from synth import B
from nav import CELL, INF


class FieldSeeds(unittest.TestCase):
    def setUp(self):
        blocked = np.zeros((16, 20), dtype=bool)
        blocked[2:12, 9] = True
        blocked[5:8, 3:7] = True
        self.grid = SimpleNamespace(nx=20, ny=16, minx=-40, miny=-32,
                                    blocked_grid=blocked, blocked=blocked.ravel().tolist(),
                                    cost=[1.6 if i % 5 == 0 else 1.0 for i in range(blocked.size)])

    def test_completed_distances_match_all_seed_search(self):
        for goal, radius in [((0, 0), 20), ((-40, -32), 13), ((0, 0), 100),
                             ((90, 0), 5), ((-22, -6), 4), ((0, 0), 0)]:
            with self.subTest(goal=goal, radius=radius):
                m = self.grid
                actual = B.Field(m, goal, radius)
                reference = B.Field(m, goal, radius)
                reference.dist = [INF] * len(m.blocked)
                r = max(radius, CELL)
                gx, gy = goal
                candidates = []
                for cell, blocked in enumerate(m.blocked):
                    row, col = divmod(cell, m.nx)
                    if blocked or not ((gx-r-m.minx)//CELL <= col <= (gx+r-m.minx)//CELL
                                       and (gy-r-m.miny)//CELL <= row <= (gy+r-m.miny)//CELL):
                        continue
                    dx = m.minx + (col + 0.5) * CELL - gx
                    dy = m.miny + (row + 0.5) * CELL - gy
                    candidates.append((math.hypot(dx, dy), cell))
                seeds = [cell for distance, cell in candidates if distance <= r]
                if not seeds and candidates:
                    seeds.append(min(candidates)[1])
                reference.heap = [(0.0, cell) for cell in seeds]
                heapq.heapify(reference.heap)
                for cell in seeds:
                    reference.dist[cell] = 0.0
                self.assertEqual(actual.dist, reference.dist)
                actual.step(5)
                reference.step(5)
                self.assertTrue(actual.done and reference.done)
                self.assertEqual(actual.dist, reference.dist)

