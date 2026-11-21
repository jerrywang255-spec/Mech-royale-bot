"""Map model for the bot: a coarse grid of the campus built once from /arena at startup.

What it gives the bot:
  - walkable(x, y)        : can a mech stand / walk here (no building in the way)?
  - los(a, b)             : is the straight line between two 3D points clear of buildings and terrain?
  - free_dist(x, y, yaw)  : how far can I walk in this direction before hitting a wall?
  - Field                 : a "distance to safety" map (Dijkstra) toward a goal area, computed a little
                            each tick so we never blow the 50 ms budget. Following it walks around buildings.

Numbers are in meters, x = east, y = north (protocol.md section 5).
"""
from __future__ import annotations

import heapq
import json
import math
import os
import time

import numpy as np

CELL = 4.0           # grid resolution in meters
INF = float("inf")


def _points_in_ring(px: np.ndarray, py: np.ndarray, ring: list) -> np.ndarray:
    """Vectorized even-odd point-in-polygon test. ring is flat [x0, y0, x1, y1, ...]."""
    xs = ring[0::2]
    ys = ring[1::2]
    n = len(xs)
    inside = np.zeros(px.shape, dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = xs[i], ys[i], xs[j], ys[j]
        if yi != yj:
            cond = (yi > py) != (yj > py)
            xcross = (xj - xi) * (py - yi) / (yj - yi) + xi
            inside ^= cond & (px < xcross)
        j = i
    return inside


class Map:
    def __init__(self, map_dir: str, step_up: float = 1.2, mech_height: float = 9.0):
        t0 = time.perf_counter()
        with open(os.path.join(map_dir, "v2", "world.json")) as f:
            world = json.load(f)
        ter = world["terrain"]
        self.tnx, self.tny = int(ter["nx"]), int(ter["ny"])
        self.tminx, self.tminy, self.tstep = float(ter["minX"]), float(ter["minY"]), float(ter["step"])
        raw = np.fromfile(os.path.join(map_dir, "v2", ter.get("file", "terrain.i16")), dtype="<i2")
        heights = raw.reshape(self.tny, self.tnx).astype(np.float32) * float(ter.get("scale", 0.01))
        self._terrain = heights

        # --- coarse grid geometry
        self.minx, self.miny = self.tminx, self.tminy
        self.nx = int(math.ceil(self.tnx * self.tstep / CELL))
        self.ny = int(math.ceil(self.tny * self.tstep / CELL))
        k = int(round(CELL / self.tstep))
        pad = np.pad(heights, ((0, self.ny * k - self.tny), (0, self.nx * k - self.tnx)), mode="edge")
        blocks = pad.reshape(self.ny, k, self.nx, k)
        ground = blocks.mean(axis=(1, 3))
        # rough = a ledge taller than step_up somewhere inside the cell (needs a jump)
        dx = np.abs(np.diff(pad, axis=1))
        dy = np.abs(np.diff(pad, axis=0))
        dx = np.pad(dx, ((0, 0), (0, 1)))
        dy = np.pad(dy, ((0, 1), (0, 0)))
        step = np.maximum(dx, dy).reshape(self.ny, k, self.nx, k).max(axis=(1, 3))
        rough = step > step_up

        # --- buildings: sample 4 points per cell so small footprints are not missed
        blocked = np.zeros((self.ny, self.nx), dtype=bool)
        top = ground.copy()
        cx = self.minx + (np.arange(self.nx) + 0.5) * CELL
        cy = self.miny + (np.arange(self.ny) + 0.5) * CELL
        offs = [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)]
        for p in world["prisms"]:
            if p.get("kind") == "bridge":
                continue  # decks you walk on or under; ignored for walking and sight
            ring = p["ring"]
            xs, ys = ring[0::2], ring[1::2]
            i0 = max(0, int((min(xs) - self.minx) // CELL) - 1)
            i1 = min(self.nx - 1, int((max(xs) - self.minx) // CELL) + 1)
            j0 = max(0, int((min(ys) - self.miny) // CELL) - 1)
            j1 = min(self.ny - 1, int((max(ys) - self.miny) // CELL) + 1)
            if i1 < i0 or j1 < j0:
                continue
            gx, gy = np.meshgrid(cx[i0:i1 + 1], cy[j0:j1 + 1])
            cover = np.zeros(gx.shape, dtype=bool)
            for ox, oy in offs:
                px, py = gx + ox, gy + oy
                ins = _points_in_ring(px, py, ring)
                for h in p.get("holes") or []:
                    ins &= ~_points_in_ring(px, py, h)
                cover |= ins
            if not cover.any():
                continue
            g = ground[j0:j1 + 1, i0:i1 + 1]
            base, ptop = float(p["base"]), float(p["top"])
            solid_low = (base - g) < mech_height + 0.5      # not an overhang you walk under
            tall = (ptop - g) > step_up
            sub = blocked[j0:j1 + 1, i0:i1 + 1]
            sub |= cover & solid_low & tall
            tsub = top[j0:j1 + 1, i0:i1 + 1]
            np.maximum(tsub, np.where(cover & solid_low, ptop, -1e9), out=tsub)

        # fine 2 m grid for line of sight: only cells whose center is inside a building count
        F = 2.0
        self.fnx = int(math.ceil(self.tnx * self.tstep / F))
        self.fny = int(math.ceil(self.tny * self.tstep / F))
        kf = int(round(F / self.tstep))
        padf = np.pad(heights, ((0, self.fny * kf - self.tny), (0, self.fnx * kf - self.tnx)), mode="edge")
        ftop = padf.reshape(self.fny, kf, self.fnx, kf).max(axis=(1, 3))
        fcx = self.minx + (np.arange(self.fnx) + 0.5) * F
        fcy = self.miny + (np.arange(self.fny) + 0.5) * F
        for p in world["prisms"]:
            if p.get("kind") == "bridge":
                continue
            ring = p["ring"]
            xs, ys = ring[0::2], ring[1::2]
            i0 = max(0, int((min(xs) - self.minx) // F))
            i1 = min(self.fnx - 1, int((max(xs) - self.minx) // F))
            j0 = max(0, int((min(ys) - self.miny) // F))
            j1 = min(self.fny - 1, int((max(ys) - self.miny) // F))
            if i1 < i0 or j1 < j0:
                continue
            gx, gy = np.meshgrid(fcx[i0:i1 + 1], fcy[j0:j1 + 1])
            ins = _points_in_ring(gx, gy, ring)
            for h in p.get("holes") or []:
                ins &= ~_points_in_ring(gx, gy, h)
            if not ins.any():
                continue
            sub = ftop[j0:j1 + 1, i0:i1 + 1]
            if float(p["base"]) - float(sub.min()) > mech_height + 0.5:
                continue  # high overhang
            np.maximum(sub, np.where(ins, float(p["top"]), -1e9), out=sub)
        self.ftop = ftop.ravel().tolist()
        self.F = F

        # edge of the map is a wall
        blocked[0, :] = blocked[-1, :] = True
        blocked[:, 0] = blocked[:, -1] = True

        # cells next to a building cost a bit more, so paths keep off walls
        near = np.zeros_like(blocked)
        near[1:, :] |= blocked[:-1, :]
        near[:-1, :] |= blocked[1:, :]
        near[:, 1:] |= blocked[:, :-1]
        near[:, :-1] |= blocked[:, 1:]
        cost = np.where(rough, 3.0, 1.0) * np.where(near & ~blocked, 1.6, 1.0)

        # plain Python lists: much faster than numpy for one-element lookups
        self.blocked = blocked.ravel().tolist()
        self.top = top.ravel().tolist()
        self.ground = ground.ravel().tolist()
        self.cost = cost.ravel().tolist()
        self.build_s = time.perf_counter() - t0

    # ---- lookups
    def cell(self, x: float, y: float) -> int:
        i = int((x - self.minx) // CELL)
        j = int((y - self.miny) // CELL)
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return -1
        return j * self.nx + i

    def center(self, c: int) -> tuple:
        j, i = divmod(c, self.nx)
        return self.minx + (i + 0.5) * CELL, self.miny + (j + 0.5) * CELL

    def walkable(self, x: float, y: float) -> bool:
        c = self.cell(x, y)
        return c >= 0 and not self.blocked[c]

    def ground_at(self, x: float, y: float) -> float:
        """Bilinear terrain height from the 1 m grid."""
        fx = (x - self.tminx) / self.tstep
        fy = (y - self.tminy) / self.tstep
        i = min(max(int(fx), 0), self.tnx - 2)
        j = min(max(int(fy), 0), self.tny - 2)
        ax, ay = min(max(fx - i, 0.0), 1.0), min(max(fy - j, 0.0), 1.0)
        t = self._terrain
        h00, h10, h01, h11 = t[j, i], t[j, i + 1], t[j + 1, i], t[j + 1, i + 1]
        return float((h00 * (1 - ax) + h10 * ax) * (1 - ay) + (h01 * (1 - ax) + h11 * ax) * ay)

    def los(self, a, b, skip: float = 3.5) -> bool:
        """True if the segment a->b (3D points) clears every building and the terrain."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        if d < 2 * skip:
            return True
        n = int(d / 2.0)
        top, nx, minx, miny = self.top, self.nx, self.minx, self.miny
        for s in range(1, n):
            f = s / n
            if f * d < skip or (1 - f) * d < skip:
                continue
            x, y = a[0] + dx * f, a[1] + dy * f
            i = int((x - minx) // CELL)
            j = int((y - miny) // CELL)
            if 0 <= i < nx and 0 <= j < self.ny and top[j * nx + i] > a[2] + dz * f:
                return False
        return True

    def los_fine(self, a, b, skip: float = 3.0) -> bool:
        """Like los() but on the 2 m grid, sampled every meter: fewer false blocks near walls."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.hypot(dx, dy)
        if d < 2 * skip:
            return True
        n = int(d)
        top, nx, ny, minx, miny, F = self.ftop, self.fnx, self.fny, self.minx, self.miny, self.F
        for s in range(1, n):
            f = s / n
            if f * d < skip or (1 - f) * d < skip:
                continue
            x, y = a[0] + dx * f, a[1] + dy * f
            i = int((x - minx) // F)
            j = int((y - miny) // F)
            if 0 <= i < nx and 0 <= j < ny and top[j * nx + i] > a[2] + dz * f:
                return False
        return True

    def free_dist(self, x: float, y: float, yaw: float, maxd: float = 16.0, start: float = 2.0) -> float:
        """How far we can go along yaw before a blocked cell (start a little out from our feet)."""
        cx, cy = math.cos(yaw), math.sin(yaw)
        d = start
        while d <= maxd:
            c = self.cell(x + cx * d, y + cy * d)
            if c < 0 or self.blocked[c]:
                return d - 2.0
            d += 2.0
        return maxd


class Field:
    """Dijkstra 'distance to goal' over the grid, run a slice at a time.

    Seeds are every walkable cell within `radius` of `goal`. Once done, `direction(x, y)` points
    along the shortest walking route to the goal area, around buildings.
    """

    def __init__(self, m: Map, goal, radius: float):
        self.m = m
        self.goal = (float(goal[0]), float(goal[1]))
        self.radius = float(radius)
        n = m.nx * m.ny
        self.dist = [INF] * n
        self.heap: list = []
        self.done = False
        gx, gy = self.goal
        r = max(radius, CELL)
        i0, i1 = int((gx - r - m.minx) // CELL), int((gx + r - m.minx) // CELL)
        j0, j1 = int((gy - r - m.miny) // CELL), int((gy + r - m.miny) // CELL)
        best, bestd = -1, INF
        for j in range(max(j0, 0), min(j1, m.ny - 1) + 1):
            for i in range(max(i0, 0), min(i1, m.nx - 1) + 1):
                c = j * m.nx + i
                if m.blocked[c]:
                    continue
                x, y = m.center(c)
                dd = math.hypot(x - gx, y - gy)
                if dd <= r:
                    self.dist[c] = 0.0
                    self.heap.append((0.0, c))
                if dd < bestd:
                    best, bestd = c, dd
        if not self.heap and best >= 0:
            self.dist[best] = 0.0
            self.heap.append((0.0, best))
        heapq.heapify(self.heap)

    def step(self, budget_s: float) -> None:
        if self.done:
            return
        m, dist, heap = self.m, self.dist, self.heap
        nx, ny, blocked, cost = m.nx, m.ny, m.blocked, m.cost
        pop, push = heapq.heappop, heapq.heappush
        deadline = time.perf_counter() + budget_s
        D = CELL * 1.4142
        count = 0
        while heap:
            d, c = pop(heap)
            if d > dist[c]:
                continue
            j, i = divmod(c, nx)
            cc = cost[c]
            for di, dj, w in ((1, 0, CELL), (-1, 0, CELL), (0, 1, CELL), (0, -1, CELL),
                              (1, 1, D), (1, -1, D), (-1, 1, D), (-1, -1, D)):
                ii, jj = i + di, j + dj
                if ii < 0 or jj < 0 or ii >= nx or jj >= ny:
                    continue
                n = jj * nx + ii
                if blocked[n]:
                    continue
                if di and dj and (blocked[j * nx + ii] or blocked[jj * nx + i]):
                    continue  # no cutting corners of buildings
                nd = d + w * 0.5 * (cc + cost[n])
                if nd < dist[n]:
                    dist[n] = nd
                    push(heap, (nd, n))
            count += 1
            if count & 255 == 0 and time.perf_counter() > deadline:
                return
        self.done = True

    def value(self, x: float, y: float) -> float:
        c = self.m.cell(x, y)
        if c < 0:
            return INF
        v = self.dist[c]
        if v == INF:  # we are on a blocked cell (roof edge, wall): use the best neighbor
            v = min((self.dist[n] for n in self._neighbors(c, 1)), default=INF)
        return v

    def _neighbors(self, c: int, r: int):
        m = self.m
        j, i = divmod(c, m.nx)
        for jj in range(max(j - r, 0), min(j + r, m.ny - 1) + 1):
            for ii in range(max(i - r, 0), min(i + r, m.nx - 1) + 1):
                if ii != i or jj != j:
                    yield jj * m.nx + ii

    def direction(self, x: float, y: float):
        """Unit vector toward the goal along walkable ground, or None if unknown."""
        m = self.m
        c = m.cell(x, y)
        if c < 0:
            return None
        here = self.dist[c]
        best, bestv = None, here if here < INF else INF
        for n in self._neighbors(c, 2):
            v = self.dist[n]
            if v == INF:
                continue
            nx_, ny_ = m.center(n)
            v += math.hypot(nx_ - x, ny_ - y) * 0.25  # prefer nearer cells for equal distance
            if v < bestv:
                best, bestv = n, v
        if best is None:
            return None
        tx, ty = m.center(best)
        dx, dy = tx - x, ty - y
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return None
        return dx / d, dy / d
