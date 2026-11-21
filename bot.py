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

import json
import math
import random
import sys
import time
import traceback

import mechfight as mf

try:
    from nav import Map, Field
except Exception:  # numpy missing or similar: run without the map instead of crashing
    Map = Field = None

TAU = 2 * math.pi
PICKUP_RANK = {"laser": 3.0, "lightning": 2.0, "mortar": 1.0, "gun": 0.0}
UPGRADE_ORDER = [
    "armor", "plating", "nanites", "laser_damage", "laser_charge", "gun_damage", "gun_rate",
    "lightning_efficiency", "lightning_range", "mortar_reload", "gun_mag", "gyros", "sensors",
    "servos", "reactor", "capacitor", "jets", "gun_velocity", "lightning_chain", "mortar_splash",
    "mortar_velocity", "laser_pierce",
]
LOUD_SOUNDS = {"gun", "laser", "laser_charge", "lightning", "mortar", "step", "boost", "jet", "reload"}
BUDGET_S = 0.020     # stop optional work after this much of the 50 ms
OBS_LISTS = ("mechs", "projectiles", "crates", "cubes", "pickups", "sounds", "beams", "events", "drops")
POSITIONED = ("mechs", "projectiles", "crates", "cubes", "pickups", "drops")


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def _is_vec3(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) >= 3 and all(isinstance(c, (int, float)) for c in v[:3])


def hp_frac(actor) -> float:
    """Health fraction of an observed actor; 1.0 (assume healthy) when hp or max_hp is hidden."""
    hp, mx = actor.get("hp"), actor.get("max_hp")
    if not isinstance(hp, (int, float)) or not isinstance(mx, (int, float)) or mx <= 0:
        return 1.0
    return max(0.0, min(1.0, hp / mx))


def clean_obs(obs):
    """Fill missing lists and drop malformed entries in place, so one bad field can't break a tick."""
    for k in OBS_LISTS:
        v = obs.get(k)
        obs[k] = [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []
    for k in POSITIONED:
        obs[k] = [e for e in obs[k] if "id" in e and _is_vec3(e.get("pos"))]
    obs["mechs"] = [m for m in obs["mechs"] if m.get("kind") in ("mech", "npc")]
    for m in obs["mechs"]:
        if not _is_vec3(m.get("vel")):
            m["vel"] = [0.0, 0.0, 0.0]
    obs["pickups"] = [p for p in obs["pickups"] if p.get("kind") in PICKUP_RANK]
    obs["sounds"] = [s for s in obs["sounds"]
                     if isinstance(s.get("bearing"), (int, float)) and "dist" in s and "kind" in s]
    me = obs["self"]
    if not isinstance(me.get("stats"), dict):
        me["stats"] = {}
    aim = me.get("aim")
    if not (isinstance(aim, (list, tuple)) and len(aim) >= 2):
        me["aim"] = [0.0, 0.0]
    for k, dflt in (("hp", 1.0), ("max_hp", 1.0), ("energy", 0.0)):
        if not isinstance(me.get(k), (int, float)):
            me[k] = dflt
    weapons = []
    for w in me.get("weapons") if isinstance(me.get("weapons"), list) else []:
        if isinstance(w, dict) and w.get("kind") in PICKUP_RANK:
            if not isinstance(w.get("stats"), dict):
                w["stats"] = {}
            weapons.append(w)
        else:
            weapons.append(None)
    me["weapons"] = (weapons + [None, None])[:2]   # two slots; fire[] is indexed by slot
    return obs


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


class Telemetry:
    """Per-match counters written to stderr, which the team can read after each match.

    One CONFIG line at start, a TELEMETRY line every PERIOD_S of match time, and a SUMMARY line at
    exit (with place/kills when the `end` message was seen). Every method swallows its own errors.
    """
    PERIOD_S = 60.0
    WINDOW_S = 10.0          # "death window": damage in the last 10 s before the final observation

    def __init__(self):
        self.taken, self.dealt, self.modes = {}, {}, {}
        self.rear_taken = 0.0
        self.kills = []
        self.recent = []         # (t, attacker, amount, weapon), last 15 s
        self.out_s = 0.0
        self.ticks = self.gaps = self.fallbacks = 0
        self.last_tick = self.last_t = None
        self.lat_hist = [0] * 61  # 1 ms buckets; the last one is >= 60 ms
        self.lat_max = 0.0
        self.hp_min = 1.0
        self.last = {}
        self.next_report = self.PERIOD_S
        self.end = None
        self.done = False

    def config(self, cfg) -> None:
        try:
            z = cfg.get("zone") or {}
            keep = {"vision": cfg.get("vision"), "match": cfg.get("match"), "mech": cfg.get("mech"),
                    "weapons": cfg.get("weapons"),
                    "zone": {"start_radius": z.get("start_radius"), "phases": z.get("phases")}}
            log("CONFIG " + json.dumps(keep, separators=(",", ":"), default=str)[:4000])
        except Exception:
            pass

    def observe(self, obs) -> None:
        try:
            t, me, tick = obs["t"], obs["self"], obs.get("tick")
            if isinstance(tick, (int, float)) and self.last_tick is not None and tick - self.last_tick > 1:
                self.gaps += 1
            if self.last_t is not None and 0 < t - self.last_t < 1 and not me.get("in_zone", True):
                self.out_s += t - self.last_t
            for ev in obs["events"]:
                kind, amt = ev.get("type"), ev.get("amount") or 0
                if kind == "damaged":
                    w = str(ev.get("weapon"))
                    self.taken[w] = self.taken.get(w, 0.0) + amt
                    if ev.get("rear"):
                        self.rear_taken += amt
                    self.recent.append((t, ev.get("by"), amt, w))
                elif kind == "hit":
                    w = str(ev.get("weapon"))
                    self.dealt[w] = self.dealt.get(w, 0.0) + amt
                elif kind == "kill":
                    self.kills.append(round(t, 1))
            self.recent = [r for r in self.recent if t - r[0] < 15][-200:]
            hp_r = me["hp"] / max(me["max_hp"], 1)
            self.hp_min = min(self.hp_min, hp_r)
            self.last = {"t": round(t, 1), "hp": round(hp_r, 2), "alive": obs.get("alive"),
                         "pos": [round(v) for v in me["pos"][:2]], "in_zone": me.get("in_zone")}
            self.ticks += 1
            self.last_tick, self.last_t = tick, t
        except Exception:
            pass

    def mode(self, m) -> None:
        k = m or "none"
        self.modes[k] = self.modes.get(k, 0) + 1

    def latency(self, ms: float) -> None:
        self.lat_hist[min(int(ms), 60)] += 1
        self.lat_max = max(self.lat_max, ms)

    def _pct(self, p: float) -> int:
        n, acc = sum(self.lat_hist), 0
        for i, c in enumerate(self.lat_hist):
            acc += c
            if n and acc >= p * n:
                return i + 1
        return 0

    def snapshot(self, final: bool = False) -> dict:
        r = lambda d: {k: round(v) for k, v in d.items()}
        s = {"last": self.last, "ticks": self.ticks, "gaps": self.gaps, "fallbacks": self.fallbacks,
             "taken": r(self.taken), "rear_taken": round(self.rear_taken), "dealt": r(self.dealt),
             "kills": self.kills, "out_zone_s": round(self.out_s, 1), "hp_min": round(self.hp_min, 2),
             "modes": self.modes, "lat_ms": {"p50": self._pct(0.5), "p99": self._pct(0.99),
                                             "max": round(self.lat_max, 1), "ge40": sum(self.lat_hist[40:])}}
        if final:
            t_end = self.last_t or 0.0
            win = [x for x in self.recent if t_end - x[0] <= self.WINDOW_S]
            by_w = {}
            for _, _, a, w in win:
                by_w[w] = by_w.get(w, 0.0) + a
            s["death_window"] = {"attackers": len({x[1] for x in win if x[1] is not None}),
                                 "damage": r(by_w)}
            s["end"] = self.end
        return s

    def periodic(self, t) -> None:
        try:
            if isinstance(t, (int, float)) and t >= self.next_report:
                self.next_report = (int(t // self.PERIOD_S) + 1) * self.PERIOD_S
                log("TELEMETRY " + json.dumps(self.snapshot(), separators=(",", ":"), default=str))
        except Exception:
            pass

    def on_end(self, msg) -> None:
        self.end = {"place": msg.get("place"), "kills": msg.get("kills")}

    def final(self) -> None:
        if self.done:
            return
        self.done = True
        try:
            log("SUMMARY " + json.dumps(self.snapshot(final=True), separators=(",", ":"), default=str))
        except Exception:
            pass


_TELEMETRY: list = []    # telemetry of the bot created by this process (read by the stdin tap and atexit)
_END: dict = {}          # the `end` message, if the tap saw one (may arrive before the bot exists)


class StdinTap:
    """Passes stdin lines through unchanged; notes the `end` message (place, kills) for the SUMMARY."""

    def __init__(self, src):
        self.src = src

    def __iter__(self):
        for line in self.src:
            if '"end"' in line:
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "end":
                        _END.update(msg)
                        for tm in _TELEMETRY[-1:]:
                            tm.on_end(msg)
                except Exception:
                    pass
            yield line


def _final_summary() -> None:
    for tm in _TELEMETRY[-1:]:
        if tm.end is None and _END:
            tm.on_end(_END)
        tm.final()


class Bot:
    name = "survivor"

    def __init__(self, hello: mf.Hello):
        self.cfg = hello.get("config") or {}
        self.rng = random.Random(hello.get("seed", 0))
        self.me_id = hello.get("slot", -1)
        mech = self.cfg.get("mech") or {}
        self.map = None
        if Map is not None:
            try:
                self.map = Map(hello["map"]["dir"], mech.get("step_up", 1.2), mech.get("height", 9.0))
                log(f"map built in {self.map.build_s:.2f}s")
            except Exception as e:  # keep playing without a map
                log(f"map failed: {e!r}")
        self.errors = 0
        self.tm = Telemetry()
        _TELEMETRY[:] = [self.tm]
        self.tm.config(self.cfg)
        self.field = None
        self.field_key = None
        self.seen = {}          # id -> last info about a mech or drone we have seen
        self.attackers = {}     # id -> last time it damaged us
        self.attacker_info = {} # id -> (last time, bearing, weapon) of its latest hit
        self.away = None        # unit vector away from 2+ threats, set by _decide
        self.threats = {}       # id -> ((x, y), health fraction), set by _decide
        self.recovering = False # low-health latch, see RECOVER_ENTER / RECOVER_EXIT
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

    # ------------------------------------------------------------------ main loop
    def act(self, obs: mf.Observation) -> mf.Action:
        t0 = time.perf_counter()
        try:
            obs = clean_obs(obs)
            self.tm.observe(obs)
            return self._act(obs, t0)
        except Exception:
            self.errors += 1
            self.tm.fallbacks += 1
            if self.errors <= 5 or self.errors % 100 == 0:
                log(f"act() failed at tick {obs.get('tick')} ({self.errors} so far):\n{traceback.format_exc()}")
            return self._fallback(obs)
        finally:
            self.tm.latency((time.perf_counter() - t0) * 1000)
            self.tm.periodic(obs.get("t") if isinstance(obs, dict) else None)

    def _fallback(self, obs) -> mf.Action:
        """Bare-minimum action after an exception: head for the next circle, hold the current aim."""
        try:
            me = obs.get("self") or {}
            x, y = me["pos"][0], me["pos"][1]
            zone = obs.get("zone") or {}
            nc = zone.get("next_center") or zone.get("center") or [x, y]
            gap = math.hypot(nc[0] - x, nc[1] - y)
            outside = not me.get("in_zone", True)
            move = [0.0, 0.0]
            if outside or gap > self._seed_radius(zone.get("next_radius", 0) or 0):
                d = None
                if self.field is not None and self.field.done:
                    d = self.field.direction(x, y)
                if d is None and gap > 1e-6:
                    d = ((nc[0] - x) / gap, (nc[1] - y) / gap)
                if d is not None:
                    move = [d[0], d[1]]
            act: mf.Action = {"move": move, "boost": bool(outside and (me.get("energy") or 0) > 15)}
            aim = me.get("aim")
            if isinstance(aim, (list, tuple)) and len(aim) >= 2:
                act["aim"] = [float(aim[0]), float(aim[1])]
            return act
        except Exception:
            return {}

    def _act(self, obs: mf.Observation, t0: float) -> mf.Action:
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}
        self._remember(obs)

        if obs.get("offer"):
            pick = self._choose_upgrade(obs["offer"])
            if pick is not None:
                act["pick"] = pick

        zone = obs["zone"]
        self._update_field(zone)

        # who is a danger right now?
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        drones = [m for m in obs["mechs"] if m["kind"] == "npc"]
        hp_r = me["hp"] / max(me["max_hp"], 1)
        target, mode = self._decide(obs, enemies, drones, hp_r)
        self.tm.mode(mode)

        move = self._zone_pull(obs)              # (vx, vy) weighted
        boost = False
        aim_yaw, aim_pitch = None, 0.0
        fire = [False, False]
        goal_label = mode
        unseen = None                            # where an out-of-view attacker probably is

        if target is not None:
            tpos = target["pos"]
            dist = math.dist(tpos[:2], (x, y))
            bearing = math.atan2(tpos[1] - y, tpos[0] - x)
            aim_yaw, aim_pitch, fire = self._weapons(obs, target, mode)
            if mode == "fight":
                move = self._add(move, self._strafe(obs, target, dist, bearing), 1.0)
            else:  # avoid / retreat: back away toward cover, keep facing them
                away = (-math.cos(bearing), -math.sin(bearing))
                if mode == "retreat" and self.away is not None:
                    away = self.away                 # several threats: away from all of them
                move = self._add(move, away, 1.6 if mode == "retreat" else 0.9)
                if mode == "retreat":     # jink sideways so a straight-line lead doesn't hit us
                    s = self._jink(t)
                    move = self._add(move, (-away[1] * s, away[0] * s), 0.9)
                boost = mode == "retreat" and me["energy"] > 25
        else:
            goal = None if self.recovering else self._pick_goal(obs, hp_r)
            if goal is not None:
                gx, gy, gid, gkind = goal
                d = math.hypot(gx - x, gy - y)
                if gkind == "drop" and d < 5:
                    move = (0.0, 0.0)      # stand in the ring
                else:
                    move = self._add(move, ((gx - x) / max(d, 1e-6), (gy - y) / max(d, 1e-6)), 1.4)
                goal_label = f"-> {gkind}"
            if self.recovering:
                goal_label = "recover"
                if self.threats:           # still being shot from out of view: get away, toward cover
                    aw = self._away(obs, self.threats)
                    if aw is not None:
                        move = self._add(move, aw, 1.6)
                        boost = me["energy"] > 25
                    (hx, hy), _ = min(self.threats.values(), key=lambda v: math.hypot(v[0][0] - x, v[0][1] - y))
                    unseen = (hx, hy, z + 8.0)
            aim_yaw, aim_pitch, fire = self._idle_aim(obs, move)

        # telegraphed damage: sidestep any charging laser aimed at us, get out of mortar impacts
        for e in enemies:
            if self._laser_on_me(obs, e):
                b = math.atan2(e["pos"][1] - y, e["pos"][0] - x)
                s = self.strafe_sign
                move = self._add(move, (-math.sin(b) * s, math.cos(b) * s), 2.5)
                boost = True
        shell = self._shell_dodge(obs)
        if shell is not None:
            (sx, sy), tl = shell
            move = self._add(move, (sx, sy), 3.0 if tl < 1.2 else 2.0)
            boost = boost or (tl < 1.2 and me["energy"] > 10)
            goal_label = f"{goal_label or ''} dodge"
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

        hide = unseen
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
            kind = ev.get("type")
            if kind == "damaged":
                self.last_hurt = t
                if ev.get("by") is not None:
                    self.attackers[ev["by"]] = t
                    self.attacker_info[ev["by"]] = (t, ev.get("bearing"), ev.get("weapon"))
                    self.hurt_bearing = ev.get("bearing")
            elif kind == "take_failed" and ev.get("id") is not None:
                self.bad_goals[ev["id"]] = t + 15
        for s in obs["sounds"]:
            self.heard.append((t - s.get("age", 0), s["bearing"], s["dist"], s["kind"]))
        self.heard = [h for h in self.heard if t - h[0] < 3.0][-30:]
        for k in [k for k, v in self.seen.items() if t - v["t"] > 12]:
            del self.seen[k]
        for k in [k for k, v in self.attacker_info.items() if t - v[0] > 30]:
            del self.attacker_info[k]

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
        """Safe inside the next circle: drift next to a wall (cover) and keep still otherwise.
        Only walls whose foot stays CAMP_MARGIN inside the next circle count, so a small late circle
        doesn't walk us out to a building beyond its edge."""
        if self.map is None:
            return (0.0, 0.0)
        me = obs["self"]
        x, y = me["pos"][0], me["pos"][1]
        m = self.map
        c = m.cell(x, y)
        if c < 0 or m.cost[c] > 1.2:   # already hugging a building
            return (0.0, 0.0)
        zone = obs["zone"]
        (ncx, ncy), safe_r = zone["next_center"][:2], zone["next_radius"] - self.CAMP_MARGIN
        best, bestd = None, 1e9
        for ang in range(0, 360, 30):
            a = math.radians(ang)
            d = m.free_dist(x, y, a, 24.0)
            if d < 22 and d < bestd and math.hypot(x + math.cos(a) * d - ncx, y + math.sin(a) * d - ncy) < safe_r:
                best, bestd = a, d
        if best is None:
            return (0.0, 0.0)
        return (math.cos(best) * 0.5, math.sin(best) * 0.5)

    # ------------------------------------------------------------------ decisions
    THREAT_RANGE = 150.0     # a visible mech aiming at us within this range counts as a threat
    THREAT_FACING = 0.35     # rad: how closely its torso must point at us
    OUTNUMBERED_K = 0.6      # retreat when hp_r < K * (sum of threat health fractions), 2+ threats
    RECOVER_ENTER = 0.35     # below this health fraction start recovering...
    RECOVER_EXIT = 0.70      # ...and keep at it (no loot, no optional fights) until back above this
    CAMP_MARGIN = 6.0        # m: a camping spot must be this far inside the next circle

    def _threats(self, obs):
        """Mechs that can hurt us now: visible ones aiming at us within THREAT_RANGE, plus any mech
        that damaged us in the last 4 s (placed from memory or the hit bearing when out of view).
        Returns id -> ((x, y), health fraction)."""
        me, t = obs["self"], obs["t"]
        x, y = me["pos"][0], me["pos"][1]
        out = {}
        for m in obs["mechs"]:
            if m["kind"] != "mech":
                continue
            mx, my = m["pos"][0], m["pos"][1]
            d = math.hypot(mx - x, my - y)
            aim = m.get("aim")
            facing = isinstance(aim, (list, tuple)) and len(aim) > 0 and isinstance(aim[0], (int, float)) \
                and abs(wrap(aim[0] - math.atan2(y - my, x - mx))) < self.THREAT_FACING
            if t - self.attackers.get(m["id"], -99) < 4 or (facing and d < self.THREAT_RANGE):
                out[m["id"]] = ((mx, my), hp_frac(m))
        for aid, (ta, bearing, weapon) in self.attacker_info.items():
            if aid in out or t - ta >= 4 or weapon in ("npc", "zone"):
                continue
            seen = self.seen.get(aid)
            if seen is not None and seen.get("kind") != "mech":
                continue
            if seen is not None and t - seen["t"] < 4:
                out[aid] = ((seen["pos"][0], seen["pos"][1]), hp_frac(seen))
            elif isinstance(bearing, (int, float)):
                out[aid] = ((x + math.cos(bearing) * 60, y + math.sin(bearing) * 60), 1.0)
        return out

    def _away(self, obs, threats):
        """Unit vector away from the threats, nearer ones weighted more; None without threats."""
        x, y = obs["self"]["pos"][0], obs["self"]["pos"][1]
        vx = vy = 0.0
        for (tx, ty), _ in threats.values():
            dx, dy = x - tx, y - ty
            d2 = max(dx * dx + dy * dy, 25.0)
            vx, vy = vx + dx / d2, vy + dy / d2
        n = math.hypot(vx, vy)
        return (vx / n, vy / n) if n > 1e-9 else None

    SHELL_HORIZON_S = 2.5    # only dodge mortar shells landing within this many seconds
    SHELL_MARGIN = 3.0       # m beyond the splash radius

    def _splash_radius(self) -> float:
        m = ((self.cfg.get("weapons") or {}).get("mortar") or {})
        for k in ("splash", "splash_radius", "blast_radius", "radius"):
            v = m.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return 7.0

    def _shell_dodge(self, obs):
        """(unit vector away from the most urgent predicted mortar impact near us, seconds to impact)
        or None. Shells follow p + v t - g t^2 / 2 (gravity from config); impact = our ground height."""
        me = obs["self"]
        x, y, z = me["pos"]
        g = float(((self.cfg.get("weapons") or {}).get("mortar") or {}).get("gravity", 22.0) or 22.0)
        danger = self._splash_radius() + self.SHELL_MARGIN
        best = None
        for p in obs["projectiles"]:
            if p.get("kind") != "mortar" or not _is_vec3(p.get("vel")):
                continue
            (px, py, pz), (vx, vy, vz) = p["pos"][:3], p["vel"][:3]
            disc = vz * vz + 2 * g * (pz - z)
            if disc < 0:
                continue
            tl = (vz + math.sqrt(disc)) / g
            if not 0 <= tl <= self.SHELL_HORIZON_S:
                continue
            lx, ly = px + vx * tl, py + vy * tl
            d = math.hypot(x - lx, y - ly)
            if d < danger and (best is None or tl < best[2]):
                best = (lx, ly, tl, d)
        if best is None:
            return None
        lx, ly, tl, d = best
        if d < 0.5:   # dead on top of us: any direction works; step forward (front toward threats)
            return (math.cos(me["aim"][0]), math.sin(me["aim"][0])), tl
        return ((x - lx) / d, (y - ly) / d), tl

    def _decide(self, obs, enemies, drones, hp_r):
        """Pick a target and a mode: fight, avoid, retreat, farm (NPC drone) or None."""
        me, t = obs["self"], obs["t"]
        pos = me["pos"]
        alive = obs.get("alive", 16)
        threats = self._threats(obs)
        self.threats = threats
        self.away = self._away(obs, threats) if len(threats) >= 2 else None
        # recovery latch: once low, keep disengaging until health is back (regeneration needs quiet)
        if alive <= 2 or hp_r >= self.RECOVER_EXIT:
            self.recovering = False
        elif hp_r < self.RECOVER_ENTER:
            self.recovering = True
        best, best_score = None, 1e18
        for e in enemies:
            d = math.dist(e["pos"], pos)
            score = d + 300 * hp_frac(e)
            if t - self.attackers.get(e["id"], -99) < 4:
                score -= 250
            if score < best_score:
                best, best_score = e, score
        if best is not None:
            d = math.dist(best["pos"][:2], pos[:2])
            e_r = hp_frac(best)
            attacking = t - self.attackers.get(best["id"], -99) < 4
            if self.recovering and not (len(threats) <= 1 and e_r < hp_r):
                return best, "retreat"       # only a lone, weaker enemy is worth fighting while low
            # outnumbered: two or more threats with more combined health than we can trade with
            if len(threats) >= 2 and alive > 2 and hp_r < self.OUTNUMBERED_K * sum(s for _, s in threats.values()):
                return best, "retreat"
            if attacking or d < 60 or alive <= 3:
                return best, "fight"
            weaker = e_r < hp_r - 0.15 or (alive <= 6 and e_r <= hp_r)
            if hp_r > 0.55 and weaker and d < 180 and self._has_shot(obs, best):
                return best, "fight"
            return best, "avoid"
        # no mechs in sight: drones that shoot us, or easy cubes while it is quiet
        quiet = all(t - v["t"] > 6 for v in self.seen.values() if v.get("kind") == "mech")
        for dr in sorted(drones, key=lambda m: math.dist(m["pos"], pos)):
            d = math.dist(dr["pos"], pos)
            if t - self.attackers.get(dr["id"], -99) < 3:
                return dr, ("fight" if hp_r > 0.4 and not self.recovering else "retreat")
            if quiet and hp_r > 0.6 and not self.recovering and d < 130 and t < 300 and self._has_shot(obs, dr):
                return dr, "farm"
        return None, None

    def _can_see(self, me, other) -> bool:
        if self.map is None:
            return True
        eye = me["pos"][2] + me["stats"].get("eye_height", 8)
        o = other["pos"]
        h = 4.5 if other["kind"] == "mech" else 1.75
        return self.map.los((me["pos"][0], me["pos"][1], eye), (o[0], o[1], o[2] + h))

    def _has_shot(self, obs, actor, map_clear=None) -> bool:
        """Direct-fire permission. An actor in this tick's obs["mechs"] beyond vision.near_range is in
        sight (the server says so), so the coarse 4 m grid LOS must not veto it. Closer actors might be
        sensed through walls, so they still need the grid LOS."""
        aid = actor.get("id")
        if any(m is actor or m.get("id") == aid for m in obs["mechs"]):
            near = (self.cfg.get("vision") or {}).get("near_range", 0) or 0
            if math.dist(actor["pos"][:2], obs["self"]["pos"][:2]) > near:
                return True
        return self._can_see(obs["self"], actor) if map_clear is None else map_clear

    def _laser_on_me(self, obs, enemy) -> bool:
        aim = enemy.get("aim")
        if not enemy.get("charging") or not isinstance(aim, (list, tuple)) or not aim:
            return False
        me = obs["self"]["pos"]
        b = math.atan2(me[1] - enemy["pos"][1], me[0] - enemy["pos"][0])
        return abs(wrap(aim[0] - b)) < 0.15

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
        map_clear = self._can_see(me, target)          # coarse grid LOS: picks the mortar arc
        clear = self._has_shot(obs, target, map_clear)  # direct-fire permission
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
        if mode in ("avoid",) or not clear:
            aim_yaw, aim_pitch = direct_yaw, direct_pitch

        tol_yaw = max(math.atan2(1.8, max(dist, 1)), 0.008) + 0.004
        # the laser is instant: aim where they will be one decision from now, not with bullet lead
        lx, ly, lz = tp[0] + tv[0] * 0.1, tp[1] + tv[1] * 0.1, tp[2] + th
        l_yaw = math.atan2(ly - y, lx - x)
        l_pitch = math.atan2(lz - muzzle, max(math.hypot(lx - x, ly - y), 1))
        l_yaw_err = abs(wrap(aim_actual[0] - l_yaw))
        l_pitch_err = abs(aim_actual[1] - l_pitch)
        yaw_err = abs(wrap(aim_actual[0] - g_yaw))
        pitch_err = abs(aim_actual[1] - g_pitch)
        shoot = mode in ("fight", "retreat", "farm") and clear

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
                    sol = self._mortar(me, target, s, map_clear)
                    if sol is not None:
                        m_yaw, m_pitch = sol
                        # use the mortar's aim only when the gun is reloading or the target is hidden
                        gun_busy = any(ww and ww["kind"] == "gun" and (ww.get("reload", 0) > 0 or ww.get("ammo", 1) == 0) for ww in weapons)
                        if gun_busy or not clear or "laser" not in kinds:
                            aim_yaw, aim_pitch = m_yaw, m_pitch
                            if abs(wrap(aim_actual[0] - m_yaw)) < 0.03 and abs(aim_actual[1] - m_pitch) < 0.03:
                                fire[i] = True
        # lob mortar at a target we may only sense through cover
        if not clear and mode == "fight":
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

    def _jink(self, t) -> int:
        """Side (+1/-1) to sidestep toward; flips at random 0.8-2.2 s intervals."""
        if t > self.strafe_until:
            self.strafe_sign = -self.strafe_sign if self.rng.random() < 0.7 else self.strafe_sign
            self.strafe_until = t + self.rng.uniform(0.8, 2.2)
        return self.strafe_sign

    def _strafe(self, obs, target, dist, bearing):
        """Sidestep across the line of fire and keep a good range for our weapons."""
        t = obs["t"]
        self._jink(t)
        kinds = [w["kind"] for w in obs["self"]["weapons"] if w]
        want = 95.0
        if "lightning" in kinds:
            want = 32.0
        if "laser" in kinds:
            want = 130.0
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
        loud = [h for h in self.heard if h[3] in LOUD_SOUNDS and h[2] in ("near", "mid") and t - h[0] < 1.5]
        if loud:
            loud.sort(key=lambda h: (h[2] != "near", -h[0]))
            return loud[0][1], 0.0, [False, False]
        recent = [v for v in self.seen.values() if v.get("kind") == "mech" and t - v["t"] < 10]
        if recent:
            v = min(recent, key=lambda v: math.dist(v["pos"][:2], (x, y)))
            return math.atan2(v["pos"][1] - y, v["pos"][0] - x), 0.0, [False, False]
        # crates in sight while things are calm: shoot them for cubes and weapons
        crate = self._crate_target(obs)
        if crate is not None:
            return crate
        sp = math.hypot(*move)
        self.scan += 0.12
        if sp > 0.3:
            base = math.atan2(move[1], move[0])
            return base + 0.9 * math.sin(self.scan * 0.6), 0.0, [False, False]
        return wrap(me["aim"][0] + 0.35), 0.0, [False, False]   # standing: turn slowly to look around

    def _crate_target(self, obs):
        me, t = obs["self"], obs["t"]
        if t - self.last_hurt < 6 or not obs["crates"] or self.recovering:
            return None
        x, y, z = me["pos"]
        muzzle = z + me["stats"].get("muzzle_height", 7)
        best = None
        for c in obs["crates"]:
            d = math.dist(c["pos"][:2], (x, y))
            if d > 110 or c["pos"][2] - z > 4:
                continue
            if self.map is not None and not self.map.los((x, y, muzzle), (c["pos"][0], c["pos"][1], c["pos"][2] + 1.2)):
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
        return PICKUP_RANK.get(w["kind"], 0) if w else -1.0

    def _wants(self, me, kind) -> bool:
        held = [w["kind"] for w in me["weapons"] if w]
        if kind in held or kind == "gun":
            return False
        return PICKUP_RANK.get(kind, 0) > self._slot_value(me)

    def _in_zone_later(self, obs, gx, gy, secs) -> bool:
        c, r = self._zone_now(obs["zone"], obs["t"] + secs)
        return math.hypot(gx - c[0], gy - c[1]) < r - 10

    def _pick_goal(self, obs, hp_r):
        """Something worth walking to while nobody is shooting: drop, weapon, cube."""
        me, t = obs["self"], obs["t"]
        x, y, z = me["pos"]
        if self._urgency(obs) > 0.75:
            return None
        recent_mech = any(v.get("kind") == "mech" and t - v["t"] < 4 for v in self.seen.values())
        best, best_score = None, 1e9
        for dp in obs.get("drops") or []:
            d = math.hypot(dp["pos"][0] - x, dp["pos"][1] - y)
            if dp.get("holder") not in (None, me.get("id")) or recent_mech:
                continue
            if d < 150 and dp.get("lands_in", 99) < d / 11 + 6:
                score = d * 0.6
                if score < best_score:
                    best, best_score = (dp["pos"][0], dp["pos"][1], dp["id"], "drop"), score
        for p in obs["pickups"]:
            if self.bad_goals.get(p["id"], -1) > t or not self._wants(me, p["kind"]):
                continue
            d = math.hypot(p["pos"][0] - x, p["pos"][1] - y)
            if d > 90 or p["pos"][2] - z > 3 or not self._in_zone_later(obs, p["pos"][0], p["pos"][1], d / 10):
                continue
            score = d - 25 * PICKUP_RANK.get(p["kind"], 0)
            if score < best_score:
                best, best_score = (p["pos"][0], p["pos"][1], p["id"], p["kind"]), score
        for c in obs["cubes"]:
            if self.bad_goals.get(c["id"], -1) > t:
                continue
            d = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
            if d > 70 or c["pos"][2] - z > 3 or (recent_mech and d > 25):
                continue
            score = d - 10
            if score < best_score:
                best, best_score = (c["pos"][0], c["pos"][1], c["id"], "cube"), score
        return best

    def _take(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        reach = (self.cfg.get("loot") or {}).get("pickup_radius", 4.0)
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

    def _choose_upgrade(self, offer):
        choices = offer.get("choices") if isinstance(offer, dict) else None
        ids = [c.get("id") if isinstance(c, dict) else None for c in choices or []]
        if not ids:
            return None
        for want in UPGRADE_ORDER:
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
    import atexit
    import signal

    sys.stdin = StdinTap(sys.stdin)        # read-only tap: notes place/kills from `end`
    atexit.register(_final_summary)
    try:  # a polite stop (SIGTERM) still runs atexit, so the SUMMARY line is written
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (AttributeError, ValueError, OSError):
        pass
    mf.run(Bot)
