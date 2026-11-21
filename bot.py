"""Survivor: a placement-first Mech Royale bot.

Scoring rewards how long you live, not kills. So the bot's priorities, in order:
  1. Never get caught by the zone: walk (around buildings) to the next circle early.
  2. Don't start fights it can't win; face anyone shooting it (rear hits do 1.5x).
  3. Retreat to cover when hurt, so health regenerates (45 HP/s after 5 s without damage).
  4. When it does fight: lead its shots, strafe, and use the best weapon it holds.
  5. Grab safe loot on the way: a laser or lightning gun, power cubes, supply drops, armor upgrades.

Each tick (10 per second) act() reads the observation and returns one action. Everything has to
finish well inside 50 ms on 1 CPU, so the heavy map work happens a slice at a time.
"""
from __future__ import annotations

import math
import random
import sys
import time

import mechfight as mf

try:
    from nav import Map, Field
except Exception:  # numpy missing or similar: run without the map instead of crashing
    Map = Field = None

TAU = 2 * math.pi
PICKUP_RANK = {"laser": 3.0, "lightning": 2.0, "mortar": 1.0, "gun": 0.0}  # Bot.PICKUP_RANK can override
UPGRADE_ORDER = [
    "armor", "plating", "nanites", "laser_damage", "laser_charge", "gun_damage", "gun_rate",
    "lightning_efficiency", "lightning_range", "mortar_reload", "gun_mag", "gyros", "sensors",
    "servos", "reactor", "capacitor", "jets", "gun_velocity", "lightning_chain", "mortar_splash",
    "mortar_velocity", "laser_pierce",
]
LOUD_SOUNDS = {"gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload"}
BUDGET_S = 0.020     # stop optional work after this much of the 50 ms


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


class Bot:
    name = "survivor"
    # tuning knobs (tested in the local simulator)
    RETREAT_HP = 0.5         # back off below this fraction of HP...
    CLOSE_FIGHT = 60.0       # ...always fight anyone this close
    FIGHT_APPROACHING = True   # fight a mech that is walking toward us instead of avoiding it
    HEAL_HOLD = False        # after a fight, stay put behind cover until healed
    LOOT_RANGE = 90.0        # how far to walk for a weapon
    FIGHT_VISIBLE = False    # fight any mech we can see (no avoiding)
    STRAFE_S = (0.8, 2.2)    # seconds between sidestep direction changes
    FIGHT_BOOST = False      # boost while sidestepping in a fight
    FARM = True              # early game: hunt drones and crates for power cubes
    FARM_UNTIL = 150.0       # seconds into the match to keep farming
    SPIN_SCAN = True         # when nothing is around, turn all the way round to spot targets
    CUBE_RANGE = 120.0       # how far to walk for a cube when it's quiet
    IGNORE_PASSIVE = True    # don't back away from mechs that aren't coming for us
    PICKUP_RANK = PICKUP_RANK
    HUNT_PASSIVE = 0.0       # if > 0: attack idle mechs in sight when HP fraction is above this
    WANT_GUN, WANT_LASER, WANT_LIGHTNING = 95.0, 130.0, 32.0   # preferred fight ranges (m)
    UPGRADE_ORDER = UPGRADE_ORDER
    OUTNUMBERED_HP = 1.01    # retreat below this HP fraction when 2+ threats are on us (1.01 = always)
    AIM_TOL_M = 1.8          # fire when the aim is within this many meters of the target point
    APPROACH_RANGE = 170.0   # how far out an approaching mech counts as a fight
    NO_CHARGE_RETREAT = False  # don't charge the laser (half speed) while running away

    def __init__(self, hello: mf.Hello):
        self.cfg = hello["config"]
        self.rng = random.Random(hello.get("seed", 0))
        self.me_id = hello.get("slot", -1)
        mech = self.cfg["mech"]
        self.map = None
        if Map is not None:
            try:
                self.map = Map(hello["map"]["dir"], mech.get("step_up", 1.2), mech.get("height", 9.0))
                log(f"map built in {self.map.build_s:.2f}s")
            except Exception as e:  # keep playing without a map
                log(f"map failed: {e!r}")
        self.field = None
        self.field_key = None
        self.seen = {}          # id -> last info about a mech or drone we have seen
        self.attackers = {}     # id -> last time it damaged us
        self.last_hurt = -99.0
        self.hurt_bearing = None
        self.heard = []         # (t, bearing, dist, kind)
        self.last_fire_t = -99.0
        self.strafe_sign = 1
        self.strafe_until = 0.0
        self.track_pos = None
        self.track_t = 0.0
        self.stuck_until = 0.0
        self.detour = None
        self.bad_goals = {}     # id -> time until which we ignore it
        self.scan = 0.0
        self.laser_wait = 0.0
        self.props = {}         # id -> (kind, x, y, z, t_seen): drones and crates we have seen

    # ------------------------------------------------------------------ main loop
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        self._remember(obs)

        if obs.get("offer"):
            act["pick"] = self._choose_upgrade(obs["offer"])

        zone = obs["zone"]
        self._update_field(zone)

        # who is a danger right now?
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        drones = [m for m in obs["mechs"] if m["kind"] == "npc"]
        hp_r = me.get("hp", 1) / max(me.get("max_hp") or 1, 1)
        target, mode = self._decide(obs, enemies, drones, hp_r)

        move = self._zone_pull(obs)              # (vx, vy) weighted
        boost = False
        aim_yaw, aim_pitch = None, 0.0
        fire = [False, False]
        goal_label = mode

        if target is not None:
            tpos = target["pos"]
            dist = math.dist(tpos[:2], (x, y))
            bearing = math.atan2(tpos[1] - y, tpos[0] - x)
            aim_yaw, aim_pitch, fire = self._weapons(obs, target, mode)
            if mode == "farm":
                # keep drones at 60-90 m: outside their 45 m aggro, inside our accurate range
                radial = 0.0 if 60 <= dist <= 90 else (1.0 if dist > 90 else -1.0)
                move = self._add(move, (math.cos(bearing) * radial, math.sin(bearing) * radial), 1.0)
            elif mode == "fight":
                move = self._add(move, self._strafe(obs, target, dist, bearing), 1.0)
                if self.FIGHT_BOOST and me["energy"] > 35 and not any(w and w["kind"] == "lightning" for w in me["weapons"]):
                    boost = True
            elif mode in ("avoid", "retreat"):  # back away toward cover, keep facing them
                move = self._add(move, (-math.cos(bearing), -math.sin(bearing)), 1.6 if mode == "retreat" else 0.9)
                boost = mode == "retreat" and me["energy"] > 25
            if self._laser_on_me(obs, target):
                move = self._add(move, (-math.sin(bearing) * self.strafe_sign, math.cos(bearing) * self.strafe_sign), 2.5)
                boost = True
        elif self.HEAL_HOLD and hp_r < 0.85 and t - self.last_hurt < 20 and self._urgency(obs) < 0.6:
            move = (0.0, 0.0)          # stand still (silent) behind cover while health comes back
            aim_yaw, aim_pitch, fire = self._idle_aim(obs, move)
            goal_label = "heal"
        else:
            goal = self._pick_goal(obs, hp_r)
            if goal is not None:
                gx, gy, gid, gkind = goal
                d = math.hypot(gx - x, gy - y)
                if gkind == "drop" and d < 5:
                    move = (0.0, 0.0)      # stand in the ring
                else:
                    move = self._add(move, ((gx - x) / max(d, 1e-6), (gy - y) / max(d, 1e-6)), 1.4)
                goal_label = f"-> {gkind}"
            aim_yaw, aim_pitch, fire = self._idle_aim(obs, move)

        # incoming mortar shell landing near us: step out of the blast
        dodge = self._shell_dodge(obs)
        if dodge is not None:
            move = self._add(move, dodge, 3.0)
            boost = boost or me["energy"] > 20

        # loot that is already within reach: take it now, whatever else we are doing
        take = self._take(obs)
        if take:
            act.update(take)
        elif target is None and not any(fire) and t - self.last_hurt > 3:
            gun = me["weapons"][0]
            if gun and gun["kind"] == "gun" and gun.get("reload", 0) == 0 \
                    and gun.get("ammo", 99) < gun["stats"].get("mag", 12) * 0.5:
                act["reload"] = True

        # zone urgency can override everything
        urgency = self._urgency(obs)
        if urgency > 0.8 and me["energy"] > 15:
            boost = True

        hide = None
        if target is not None and mode in ("retreat", "avoid"):
            tp = target["pos"]
            hide = (tp[0], tp[1], tp[2] + 8.0)
        mv, jump, boost2 = self._steer(obs, move, mode, hide)
        boost = boost or boost2
        act.update(move=mv, boost=boost, jump=jump,
                   aim=[aim_yaw if aim_yaw is not None else me["aim"][0], aim_pitch],
                   fire=fire)
        if any(fire):
            self.last_fire_t = t

        # optional work with whatever time is left: grow the zone field
        if self.field is not None and not self.field.done:
            left = BUDGET_S - (time.perf_counter() - t0)
            if left > 0.002:
                self.field.step(left)
        mf.draw.label(f"{goal_label} hp {me['hp']:.0f} z{urgency:.1f}")
        return act

    # ------------------------------------------------------------------ memory
    def _remember(self, obs) -> None:
        t = obs["t"]
        for m in obs["mechs"]:
            self.seen[m["id"]] = {**m, "t": t}
        for ev in obs["events"]:
            if ev["type"] == "damaged":
                self.last_hurt = t
                if ev.get("by") is not None:
                    self.attackers[ev["by"]] = t
                    self.hurt_bearing = ev.get("bearing")
            elif ev["type"] == "take_failed":
                self.bad_goals[ev["id"]] = t + 15
        for s in obs["sounds"]:
            self.heard.append((t - s.get("age", 0), s["bearing"], s["dist"], s["kind"]))
        self.heard = [h for h in self.heard if t - h[0] < 3.0][-30:]
        for k in [k for k, v in self.seen.items() if t - v["t"] > 12]:
            del self.seen[k]
        me = obs["self"]["pos"]
        listed = set()
        for m in obs["mechs"]:
            if m["kind"] == "npc":
                self.props[m["id"]] = ("npc", m["pos"][0], m["pos"][1], m["pos"][2], t)
                listed.add(m["id"])
        for c in obs["crates"]:
            self.props[c["id"]] = ("crate", c["pos"][0], c["pos"][1], c["pos"][2], t)
            listed.add(c["id"])
        for k, (kind, px, py, pz, ts) in list(self.props.items()):
            near = math.hypot(px - me[0], py - me[1])
            if k not in listed and (near < 35 or t - ts > 60):
                del self.props[k]   # gone (destroyed or wandered off)

    # ------------------------------------------------------------------ zone
    def _zone_now(self, zone, t):
        """Center and radius of the zone circle at time t (it moves linearly while shrinking)."""
        ss, se = zone["shrink_start"], zone["shrink_end"]
        if t <= ss or se <= ss:
            return zone["center"], zone["radius"]
        f = min(1.0, (t - ss) / (se - ss))
        c = [zone["center"][i] + (zone["next_center"][i] - zone["center"][i]) * f for i in range(2)]
        return c, zone["radius"] + (zone["next_radius"] - zone["radius"]) * f

    def _seed_radius(self, r: float) -> float:
        return r * 0.6 if r > 40 else max(r * 0.5, 6.0)

    def _update_field(self, zone) -> None:
        if self.map is None:
            return
        nc, nr = zone["next_center"], zone["next_radius"]
        key = (round(nc[0]), round(nc[1]), round(nr))
        if key != self.field_key:
            self.field_key = key
            try:
                self.field = Field(self.map, nc, self._seed_radius(nr))
            except Exception as e:
                log(f"field failed: {e!r}")
                self.field = None

    def _urgency(self, obs) -> float:
        """0 = relaxed, 1 = run for the zone now."""
        me, zone, t = obs["self"], obs["zone"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        if not me.get("in_zone", True):
            return 1.0
        nc, nr = zone["next_center"], zone["next_radius"]
        d_next = math.hypot(x - nc[0], y - nc[1])
        if d_next <= self._seed_radius(nr) + 4:
            return 0.0
        path = d_next
        if self.field is not None and self.field.done:
            v = self.field.value(x, y)
            if v < float("inf"):
                path = v
        speed = me["stats"].get("walk_speed", 11.7)
        eta = path / speed
        time_left = max(zone["shrink_end"] - t, 1.0)
        c, r = self._zone_now(zone, t + 3)
        edge = r - math.hypot(x - c[0], y - c[1])   # meters inside the circle 3 s from now
        u = max(eta * 1.4 / time_left, 0.25 if d_next > nr else 0.1)
        if edge < 15:
            u = max(u, 0.9)
        return min(u, 1.0)

    def _zone_pull(self, obs):
        """A movement vector toward safety, weighted by urgency."""
        me, zone = obs["self"], obs["zone"]
        x, y = me["pos"][0], me["pos"][1]
        u = self._urgency(obs)
        if u <= 0.0:
            return self._camp(obs)
        d = None
        if self.field is not None and self.field.done:
            d = self.field.direction(x, y)
        if d is None:
            nc = zone["next_center"]
            dx, dy = nc[0] - x, nc[1] - y
            n = math.hypot(dx, dy) or 1.0
            d = (dx / n, dy / n)
        w = 0.6 + 3.0 * u
        return (d[0] * w, d[1] * w)

    def _camp(self, obs):
        """Safe inside the next circle: drift next to a wall (cover) and keep still otherwise."""
        if self.map is None:
            return (0.0, 0.0)
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        m = self.map
        c = m.cell(x, y)
        if c < 0 or m.cost[c] > 1.2:   # already hugging a building
            return (0.0, 0.0)
        best, bestd = None, 1e9
        for ang in range(0, 360, 30):
            a = math.radians(ang)
            d = m.free_dist(x, y, a, 24.0)
            if d < 22 and d < bestd:
                best, bestd = a, d
        if best is None:
            return (0.0, 0.0)
        return (math.cos(best) * 0.5, math.sin(best) * 0.5)

    # ------------------------------------------------------------------ decisions
    def _decide(self, obs, enemies, drones, hp_r):
        """Pick a target and a mode: fight, avoid, retreat, farm (NPC drone) or None."""
        me, t = obs["self"], obs["t"]
        pos = me["pos"]
        alive = obs.get("alive", 16)
        best, best_score = None, 1e18
        passive = []
        if self.IGNORE_PASSIVE:
            threats = [e for e in enemies if self._threat(obs, e)]
            passive = [e for e in enemies if e not in threats]
            enemies = threats
        if not enemies and passive and self.HUNT_PASSIVE > 0 and hp_r > self.HUNT_PASSIVE:
            vis = [e for e in passive if math.dist(e["pos"][:2], pos[:2]) < 160 and self._can_see(me, e)]
            if vis:
                return min(vis, key=lambda e: math.dist(e["pos"], pos)), "fight"
        for e in enemies:
            d = math.dist(e["pos"], pos)
            score = d + 300 * self._hp_frac(e)
            if t - self.attackers.get(e["id"], -99) < 4:
                score -= 250
            if score < best_score:
                best, best_score = e, score
        if best is not None:
            d = math.dist(best["pos"][:2], pos[:2])
            e_r = self._hp_frac(best)
            attacking = t - self.attackers.get(best["id"], -99) < 4
            if hp_r < self.RETREAT_HP and e_r > hp_r and alive > 2:
                return best, "retreat"
            if self.OUTNUMBERED_HP > 0 and len(enemies) >= 2 and hp_r < self.OUTNUMBERED_HP and alive > 2:
                return best, "retreat"
            if attacking or d < self.CLOSE_FIGHT or alive <= 3:
                return best, "fight"
            if self.FIGHT_APPROACHING and d < self.APPROACH_RANGE and self._can_see(me, best):
                ev = best.get("vel", [0, 0, 0])
                closing = -(ev[0] * (best["pos"][0] - pos[0]) + ev[1] * (best["pos"][1] - pos[1])) / max(d, 1)
                if closing > 2.0:
                    return best, "fight"
            if self.FIGHT_VISIBLE and d < 200 and self._can_see(me, best):
                return best, "fight"
            weaker = e_r < hp_r - 0.15 or (alive <= 6 and e_r <= hp_r)
            if hp_r > 0.55 and weaker and d < 180 and self._can_see(me, best):
                return best, "fight"
            return best, "avoid"
        # no mechs in sight: drones that shoot us, or easy cubes while it is quiet
        quiet = (not enemies) if self.IGNORE_PASSIVE else all(t - v["t"] > 6 for v in self.seen.values() if v.get("kind") == "mech")
        quiet = quiet and t - max([v for k, v in self.attackers.items() if k < 100] or [-99]) > 6
        for dr in sorted(drones, key=lambda m: math.dist(m["pos"], pos)):
            d = math.dist(dr["pos"], pos)
            if t - self.attackers.get(dr["id"], -99) < 3:
                return dr, ("fight" if hp_r > 0.4 else "retreat")
            if quiet and hp_r > 0.5 and d < 140 and t < max(self.FARM_UNTIL, 300) and self._can_see(me, dr):
                return dr, "farm"
        return None, None

    @staticmethod
    def _hp_frac(e) -> float:
        """Health fraction of another mech; hidden or missing HP counts as full."""
        mx = e.get("max_hp") or 0
        hp = e.get("hp")
        if not mx or hp is None:
            return 1.0
        return max(0.0, min(1.0, hp / mx))

    def _threat(self, obs, e) -> bool:
        """Is this mech a danger: shot us lately, walking at us, or aiming at us?"""
        t, me = obs["t"], obs["self"]["pos"]
        if t - self.attackers.get(e["id"], -99) < 10:
            return True
        dx, dy = me[0] - e["pos"][0], me[1] - e["pos"][1]
        d = math.hypot(dx, dy) or 1.0
        ev = e.get("vel", [0, 0, 0])
        if (ev[0] * dx + ev[1] * dy) / d > 2.5 and d < 200:
            return True
        if e.get("aim") and abs(wrap(e["aim"][0] - math.atan2(dy, dx))) < 0.35 and d < 150:
            return True
        return d < 35

    def _can_see(self, me, other) -> bool:
        if self.map is None:
            return True
        eye = me["pos"][2] + me["stats"].get("eye_height", 8)
        o = other["pos"]
        h = 4.5 if other["kind"] == "mech" else 1.75
        return self.map.los_fine((me["pos"][0], me["pos"][1], eye), (o[0], o[1], o[2] + h))

    def _laser_on_me(self, obs, enemy) -> bool:
        if not enemy.get("charging"):
            return False
        me = obs["self"]["pos"]
        b = math.atan2(me[1] - enemy["pos"][1], me[0] - enemy["pos"][0])
        return abs(wrap(enemy["aim"][0] - b)) < 0.15

    # ------------------------------------------------------------------ weapons
    def _weapons(self, obs, target, mode):
        """Aim and trigger decisions for both slots."""
        me = obs["self"]
        x, y, z = me["pos"]
        st = me["stats"]
        muzzle = z + st.get("muzzle_height", 7)
        tp, tv = target["pos"], target.get("vel", [0, 0, 0])
        th = 4.5 if target["kind"] == "mech" else 1.75
        cubes = target.get("cubes", 0) or 0
        th *= 1 + 0.05 * cubes
        dist = math.dist(tp[:2], (x, y))
        visible = self._can_see(me, target)
        fire = [False, False]
        weapons = me["weapons"]
        kinds = [w["kind"] if w else None for w in weapons]
        aim_actual = me["aim"]

        # straight-line lead for the gun (and good enough for the laser, which is instant)
        def lead(speed):
            px, py, pz = tp[0], tp[1], tp[2] + th
            for _ in range(3):
                tt = math.dist((px, py, pz), (x, y, muzzle)) / speed
                px, py = tp[0] + tv[0] * tt, tp[1] + tv[1] * tt
                pz = tp[2] + th + max(min(tv[2], 5), -5) * tt
            return math.atan2(py - y, px - x), math.atan2(pz - muzzle, math.hypot(px - x, py - y))

        gun_speed = 252.0
        for w in weapons:
            if w and w["kind"] == "gun":
                gun_speed = w["stats"].get("speed", 252.0)
        g_yaw, g_pitch = lead(gun_speed)
        direct_yaw, direct_pitch = math.atan2(tp[1] - y, tp[0] - x), math.atan2(tp[2] + th - muzzle, max(dist, 1))
        aim_yaw, aim_pitch = g_yaw, g_pitch
        if mode in ("avoid",) or not visible:
            aim_yaw, aim_pitch = direct_yaw, direct_pitch

        tol_yaw = max(math.atan2(self.AIM_TOL_M, max(dist, 1)), 0.008) + 0.004
        # the laser is instant: aim where they will be one decision from now, not with bullet lead
        lx, ly, lz = tp[0] + tv[0] * 0.1, tp[1] + tv[1] * 0.1, tp[2] + th
        l_yaw = math.atan2(ly - y, lx - x)
        l_pitch = math.atan2(lz - muzzle, max(math.hypot(lx - x, ly - y), 1))
        l_yaw_err = abs(wrap(aim_actual[0] - l_yaw))
        l_pitch_err = abs(aim_actual[1] - l_pitch)
        yaw_err = abs(wrap(aim_actual[0] - g_yaw))
        pitch_err = abs(aim_actual[1] - g_pitch)
        shoot = mode in ("fight", "retreat", "farm") and visible

        for i, w in enumerate(weapons):
            if not w or not shoot:
                continue
            k, s = w["kind"], w["stats"]
            if k == "gun":
                if w.get("reload", 0) == 0 and w.get("ammo", 1) > 0 and dist < s.get("range", 230) \
                        and yaw_err < tol_yaw and pitch_err < 0.06:
                    fire[i] = True
            elif k == "lightning":
                if dist < s.get("range", 45) * 0.92 and me["energy"] >= s.get("energy", 3) + 2 \
                        and abs(wrap(aim_actual[0] - direct_yaw)) < math.radians(s.get("cone_deg", 30)) * 0.4:
                    fire[i] = True
            elif k == "laser":
                charge = w.get("charge", 0) or 0
                full = s.get("charge", 0.75)
                if mode == "retreat" and self.NO_CHARGE_RETREAT and charge <= 0:
                    continue
                if dist < s.get("range", 280) * 0.95 and w.get("cooldown", 0) <= 0.05:
                    if charge < full * 0.97:
                        fire[i] = True                          # keep charging
                    elif l_yaw_err < tol_yaw and l_pitch_err < 0.04:
                        fire[i] = False                         # let go: beam fires
                    else:
                        fire[i] = charge < full + s.get("hold_s", 2) * 0.8  # hold until lined up
                    if charge > full * 0.5:
                        aim_yaw, aim_pitch = l_yaw, l_pitch
            elif k == "mortar":
                if 28 < dist < 230 and w.get("cooldown", 0) <= 0:
                    sol = self._mortar(me, target, s, visible)
                    if sol is not None:
                        m_yaw, m_pitch = sol
                        # use the mortar's aim only when the gun is reloading or the target is hidden
                        gun_busy = any(ww and ww["kind"] == "gun" and (ww.get("reload", 0) > 0 or ww.get("ammo", 1) == 0) for ww in weapons)
                        if gun_busy or not visible or "laser" not in kinds:
                            aim_yaw, aim_pitch = m_yaw, m_pitch
                            if abs(wrap(aim_actual[0] - m_yaw)) < 0.03 and abs(aim_actual[1] - m_pitch) < 0.03:
                                fire[i] = True
        # lob mortar at a remembered target behind cover
        if not visible and mode == "fight":
            for i, w in enumerate(weapons):
                if w and w["kind"] == "mortar" and w.get("cooldown", 0) <= 0 and 40 < dist < 230:
                    sol = self._mortar(me, target, w["stats"], False)
                    if sol:
                        aim_yaw, aim_pitch = sol
                        if abs(wrap(aim_actual[0] - sol[0])) < 0.03 and abs(aim_actual[1] - sol[1]) < 0.03:
                            fire[i] = True
        lo, hi = me["stats"].get("min_pitch", -0.7), me["stats"].get("max_pitch", 1.3)
        return aim_yaw, max(lo, min(hi, aim_pitch)), fire

    def _mortar(self, me, target, s, visible):
        """Ballistic aim (low arc if we can see them, high arc to lob over buildings)."""
        x, y, z = me["pos"]
        tube = z + me["stats"].get("height", 9) + 0.5
        v, g = s.get("speed", 72), s.get("gravity", 22)
        tp, tv = target["pos"], target.get("vel", [0, 0, 0])
        px, py = tp[0], tp[1]
        tof = 0.0
        for _ in range(3):
            R = math.hypot(px - x, py - y)
            h = tp[2] + 1.0 - tube
            disc = v ** 4 - g * (g * R * R + 2 * h * v * v)
            if disc < 0 or R < 1:
                return None
            root = math.sqrt(disc)
            ang = math.atan((v * v - root) / (g * R)) if visible else math.atan((v * v + root) / (g * R))
            tof = R / (v * math.cos(ang))
            px, py = tp[0] + tv[0] * tof, tp[1] + tv[1] * tof
        if ang > me["stats"].get("max_pitch", 1.3):
            return None
        if self.map is not None and self.map.free_dist(x, y, math.atan2(py - y, px - x), 8.0) < 6:
            return None  # a wall in front of the tube: the blast would hit us
        return math.atan2(py - y, px - x), ang

    def _shell_dodge(self, obs):
        """Predict where visible mortar shells land; if one lands near us, return a move away from it."""
        me = obs["self"]
        x, y, z = me["pos"]
        g = self.cfg["weapons"]["mortar"].get("gravity", 22.0)
        radius = self.cfg["weapons"]["mortar"].get("splash_radius", 7.0) + 3.5
        best = None
        for p in obs.get("projectiles") or []:
            if p.get("kind") != "mortar":
                continue
            px, py, pz = p["pos"]
            vx, vy, vz = p["vel"]
            # time until the shell is back at our height (z): pz + vz t - g t^2 / 2 = z
            a, b, c = -0.5 * g, vz, pz - z
            disc = b * b - 4 * a * c
            if disc < 0:
                continue
            tl = (-b - math.sqrt(disc)) / (2 * a)
            if tl <= 0 or tl > 4.0:
                continue
            lx, ly = px + vx * tl, py + vy * tl
            mx, my = x + me["vel"][0] * tl, y + me["vel"][1] * tl
            d = math.hypot(mx - lx, my - ly)
            if d < radius and (best is None or tl < best[0]):
                best = (tl, lx, ly, d)
        if best is None:
            return None
        _, lx, ly, d = best
        dx, dy = x - lx, y - ly
        n = math.hypot(dx, dy)
        if n < 0.5:
            a = math.atan2(me["vel"][1], me["vel"][0]) + math.pi / 2
            return (math.cos(a), math.sin(a))
        return (dx / n, dy / n)

    def _strafe(self, obs, target, dist, bearing):
        """Sidestep across the line of fire and keep a good range for our weapons."""
        t = obs["t"]
        if t > self.strafe_until:
            self.strafe_sign = -self.strafe_sign if self.rng.random() < 0.7 else self.strafe_sign
            self.strafe_until = t + self.rng.uniform(*self.STRAFE_S)
        kinds = [w["kind"] for w in obs["self"]["weapons"] if w]
        want = self.WANT_GUN
        if "lightning" in kinds:
            want = self.WANT_LIGHTNING
        if "laser" in kinds:
            want = self.WANT_LASER
        if target["kind"] == "mech" and "lightning" in (target.get("weapons") or []) and "lightning" not in kinds:
            want = max(want, 60.0)
        radial = max(-1.0, min(1.0, (dist - want) / want))
        sx, sy = -math.sin(bearing) * self.strafe_sign, math.cos(bearing) * self.strafe_sign
        rx, ry = math.cos(bearing) * radial, math.sin(bearing) * radial
        return (sx + rx * 0.8, sy + ry * 0.8)

    # ------------------------------------------------------------------ looking around
    def _idle_aim(self, obs, move):
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        if t - self.last_hurt < 2.5 and self.hurt_bearing is not None:
            return self.hurt_bearing, 0.0, [False, False]
        weapon_kinds = ("gun", "laser", "laser_charge", "lightning", "mortar")
        loud = [h for h in self.heard if h[3] in weapon_kinds and h[2] == "near" and t - h[0] < 1.0]
        if loud:
            loud.sort(key=lambda h: (h[2] != "near", -h[0]))
            return loud[0][1], 0.0, [False, False]
        recent = [v for v in self.seen.values() if v.get("kind") == "mech" and t - v["t"] < 10
                  and (not self.IGNORE_PASSIVE or t - self.attackers.get(v["id"], -99) < 10)]
        if recent:
            v = min(recent, key=lambda v: math.dist(v["pos"][:2], (x, y)))
            return math.atan2(v["pos"][1] - y, v["pos"][0] - x), 0.0, [False, False]
        # crates in sight while things are calm: shoot them for cubes and weapons
        crate = self._crate_target(obs)
        if crate is not None:
            return crate
        sp = math.hypot(*move)
        self.scan += 0.12
        if self.SPIN_SCAN and t - self.last_hurt > 5:
            return wrap(me["aim"][0] + 0.38), 0.0, [False, False]   # full turn every ~1.7 s
        if sp > 0.3:
            base = math.atan2(move[1], move[0])
            return base + 0.9 * math.sin(self.scan * 0.6), 0.0, [False, False]
        return wrap(me["aim"][0] + 0.35), 0.0, [False, False]   # standing: turn slowly to look around

    def _crate_target(self, obs):
        me, t = obs["self"], obs["t"]
        if t - self.last_hurt < 6 or not obs["crates"]:
            return None
        x, y, z = me["pos"]
        muzzle = z + me["stats"].get("muzzle_height", 7)
        best = None
        for c in obs["crates"]:
            d = math.dist(c["pos"][:2], (x, y))
            if d > 110 or c["pos"][2] - z > 4:
                continue
            if self.map is not None and not self.map.los_fine((x, y, muzzle), (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
                continue
            if best is None or d < best[0]:
                best = (d, c)
        if best is None:
            return None
        d, c = best
        yaw = math.atan2(c["pos"][1] - y, c["pos"][0] - x)
        pitch = math.atan2(c["pos"][2] + 1.2 - muzzle, max(d, 1))
        fire = [False, False]
        for i, w in enumerate(me["weapons"]):
            if w and w["kind"] == "gun" and w.get("reload", 0) == 0 and w.get("ammo", 1) > 0:
                if abs(wrap(me["aim"][0] - yaw)) < max(math.atan2(1.0, d), 0.01) and abs(me["aim"][1] - pitch) < 0.05:
                    fire[i] = True
        return yaw, pitch, fire

    # ------------------------------------------------------------------ loot
    def _slot_value(self, me):
        w = me["weapons"][1] if len(me["weapons"]) > 1 else None
        return self.PICKUP_RANK.get(w["kind"], 0) if w else -1.0

    def _wants(self, me, kind) -> bool:
        held = [w["kind"] for w in me["weapons"] if w]
        if kind in held or kind == "gun":
            return False
        return self.PICKUP_RANK.get(kind, 0) > self._slot_value(me)

    def _in_zone_later(self, obs, gx, gy, secs) -> bool:
        c, r = self._zone_now(obs["zone"], obs["t"] + secs)
        return math.hypot(gx - c[0], gy - c[1]) < r - 10

    def _pick_goal(self, obs, hp_r):
        """Something worth walking to while nobody is shooting: drop, weapon, cube."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        if self._urgency(obs) > 0.75:
            return None
        if self.IGNORE_PASSIVE:
            recent_mech = any(self._threat(obs, m) for m in obs["mechs"] if m["kind"] == "mech") \
                or t - max([v for k, v in self.attackers.items() if k < 100] or [-99]) < 5
        else:
            recent_mech = any(v.get("kind") == "mech" and t - v["t"] < 4 for v in self.seen.values())
        best, best_score = None, 1e9
        for dp in obs.get("drops") or []:
            d = math.hypot(dp["pos"][0] - x, dp["pos"][1] - y)
            if dp.get("holder") not in (None, me["id"]) or recent_mech:
                continue
            if d < 150 and dp.get("lands_in", 99) < d / 11 + 6:
                score = d * 0.6
                if score < best_score:
                    best, best_score = (dp["pos"][0], dp["pos"][1], dp["id"], "drop"), score
        for p in obs["pickups"]:
            if self.bad_goals.get(p["id"], -1) > t or not self._wants(me, p["kind"]):
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            if d > self.LOOT_RANGE or p["pos"][2] - z > 3 or not self._in_zone_later(obs, p["pos"][0], p["pos"][1], d / 10):
                continue
            score = d - 25 * self.PICKUP_RANK.get(p["kind"], 0)
            if score < best_score:
                best, best_score = (p["pos"][0], p["pos"][1], p["id"], p["kind"]), score
        if self.FARM and t < self.FARM_UNTIL and not recent_mech and hp_r > 0.5:
            for k, (kind, px, py, pz, ts) in self.props.items():
                if pz - z > 3 or self.bad_goals.get(k, -1) > t:
                    continue
                d = math.hypot(px - x, py - y)
                stand = 75.0 if kind == "npc" else 50.0
                if d < stand or d > 260 or not self._in_zone_later(obs, px, py, d / 11):
                    continue
                score = d - stand + (40 if kind == "crate" else 0)
                if score < best_score:
                    # walk to a point `stand` meters short of it
                    f = (d - stand) / d
                    best, best_score = (x + (px - x) * f, y + (py - y) * f, k, kind), score
        for c in obs["cubes"]:
            if self.bad_goals.get(c["id"], -1) > t:
                continue
            d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
            if d > self.CUBE_RANGE or c["pos"][2] - z > 3 or (recent_mech and d > 25):
                continue
            score = d - 10
            if score < best_score:
                best, best_score = (c["pos"][0], c["pos"][1], c["id"], "cube"), score
        return best

    def _take(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        reach = self.cfg["loot"].get("pickup_radius", 4.0)
        for w in me["weapons"]:
            if w and w["kind"] == "laser" and (w.get("charge") or 0) > 0:
                return None  # can't swap while charging
        for p in obs["pickups"]:
            if math.hypot(p["pos"][0] - x, p["pos"][1] - y) < reach - 0.3 and abs(p["pos"][2] - z) < 3.8 \
                    and self._wants(me, p["kind"]):
                if me["weapons"][1] is None:
                    return {"take": p["id"]}
                return {"take": p["id"], "drop": 1}
        return None

    def _choose_upgrade(self, offer) -> int:
        ids = [c["id"] for c in offer["choices"]]
        for want in self.UPGRADE_ORDER:
            if want in ids:
                return ids.index(want)
        return 0

    # ------------------------------------------------------------------ movement
    @staticmethod
    def _add(a, b, w):
        return (a[0] + b[0] * w, a[1] + b[1] * w)

    def _steer(self, obs, move, mode, hide=None):
        """Turn a wish vector into a safe move: avoid walls, stay in the zone, get unstuck."""
        me, t = obs["self"], obs["t"]
        x, y, _ = me["pos"]
        mag = math.hypot(*move)
        jump = boost = False

        # stuck detection: we keep asking to move but barely go anywhere
        if self.track_pos is None or t - self.track_t > 0.8:
            if self.track_pos is not None and mag > 0.3 and math.dist(self.track_pos, (x, y)) < 1.2 and t > 2:
                self.stuck_until = t + 0.9
                self.detour = self.rng.uniform(-math.pi, math.pi)
            self.track_pos, self.track_t = (x, y), t
        if t < self.stuck_until:
            jump = me.get("grounded", True)
            boost = me["energy"] > 10
            if self.detour is not None and mag > 0.1:
                base = math.atan2(move[1], move[0])
                a = base + 0.6 * math.copysign(1, wrap(self.detour))
                return [math.cos(a), math.sin(a)], jump, boost

        if mag < 0.05:
            return [0.0, 0.0], jump, boost
        want = math.atan2(move[1], move[0])
        if self.map is None:
            return [math.cos(want), math.sin(want)], jump, boost

        # score 16 headings: close to what we want, not into a wall, not out of the zone
        c_now, r_now = self._zone_now(obs["zone"], t + 2)
        inside = math.hypot(x - c_now[0], y - c_now[1]) < r_now
        best, best_s = want, -1e9
        for k in range(16):
            a = want + (k if k < 8 else k - 16) * (TAU / 16)
            s = math.cos(a - want) * 2.0
            free = self.map.free_dist(x, y, a, 12.0)
            if free < 4:
                s -= 3.0
            elif free < 8:
                s -= 0.8
            if inside:
                px, py = x + math.cos(a) * 12, y + math.sin(a) * 12
                if math.hypot(px - c_now[0], py - c_now[1]) > r_now - 3:
                    s -= 1.5
            if hide is not None and free >= 8:
                px, py = x + math.cos(a) * 10, y + math.sin(a) * 10
                if not self.map.los(hide, (px, py, self.map.ground_at(px, py) + 6.0)):
                    s += 1.4          # this way puts a building between us and them
            if s > best_s:
                best, best_s = a, s
        speed = min(1.0, mag)
        return [math.cos(best) * speed, math.sin(best) * speed], jump, boost


if __name__ == "__main__":
    mf.run(Bot)
