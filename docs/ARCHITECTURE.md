# Bot architecture

Update this file when module responsibilities or major decision flows change.

> **Drift note (2026-11-21, E008):** the workspace `bot.py` (baseline SHA-256 `97bce237…`) is not the E001–E007 candidate this file describes. It has no `clean_obs()`, `Telemetry`, `_threats()`, `recovering` latch or `_has_shot()`; `_decide()` uses `_threat()` and a `_retreating` flag instead. The "Current decision flow" below is therefore partly stale, and 43 of the 50 older tests in `tests/` fail or error against this `bot.py` (before and after E008). The "Cover episode" section describes code that is present.

## Cover episode (E008, `Bot.COVER`, env `SURVIVOR_COVER=0` disables)

Runs in `act()` right after `_decide()`; when no episode is active and none starts, the tick is exactly the old path.

1. `_cover_update()` keeps 3 s windows of mech damage taken (`damaged` with `by`, not zone/npc) and damage dealt (`hit` on anything not known as a drone or crate), and the last hit bearing per attacker.
2. **Start** when: ≥ 2 mech hits and ≥ `COVER_TAKEN` (10%) of max HP taken in the window, dealt < `COVER_RATIO` (0.5) × taken, `alive > 2`, zone urgency < 0.5, map loaded, not inside the cooldown, and not finishing a lone visible threat at ≤ 25% HP while we have ≥ 30%.
3. `_find_cover()` scores 12 bearings × 6 radii (6–36 m), away-from-the-shooter bearings first. A candidate must be straight-line walkable (`free_dist`), not a ledge cell, inside the zone after travel + 5 s with time left to reach the next circle (`_cover_zone_ok`), and hidden from up to 3 threats (visible threats via `_threat()`, else remembered positions, else 100 m along the hit bearing). Coarse `Map.los` screens; `Map.los_fine` validates with ±2.5 m body offsets and each shooter stepped ±10 m sideways. Bounded by the time left in a 14 ms slice; with < 4 ms left (e.g. a new zone Field this tick) the search waits a tick.
4. `_cover_move()` walks straight to the spot (bypassing `_steer`), boosts with energy > 25, keeps only gun/mortar shots computed against a mech target, never charges the laser; on arrival it stands still, holds fire, and faces the nearest threat.
5. **Re-pick** (≤ 6 per episode) when the spot is seen (checked every 0.5 s or on a hit), when hit while holding it, or after no progress for 1 s (that heading is excluded; a second stall ends the episode).
6. **End** on `healed` (arrived, ≥ 90% HP, 5 s without mech damage), `zone` (urgency ≥ 0.7), `timeout` (25 s), `final` (alive ≤ 2), `exposed`/`stuck` (no new spot). Cooldown before the next start: 4 s, or 1 s after `exposed`/`stuck`. The damage windows are cleared at the end.
7. Any exception inside the cover logic logs once and sets `COVER = False` for the rest of the match.

Stderr lines (low rate): `CONFIG mech k=v …` once at start (to check the assumed 5 s regeneration delay against the real config keys), `COVER start t= hp= taken= dealt= threats= dist= ms=`, `COVER end t= reason= dur= reached_hp= reached_s= hp= repicks=`, and one `LAST t= hp= hp_r= alive= mode= cover=` at interpreter exit (last observation; not written if the process is killed).

Tools: `scripts/exchange_sim.py` (synthetic exchange harness, not a match simulator), `scripts/bench_cover.py` (trigger-tick latency), `scripts/compare_runs.py` (paired placement comparison from a real runner).

## Zone-first dodge arbitration (E009, `Bot.ZONE_FIRST`, env `SURVIVOR_ZONEFIRST=0` disables)

In `act()` the mortar-shell dodge (`_shell_dodge`) is added to the move vector once, after the zone, cover and loot decisions, with weight 3.0. While `_urgency() > ZONE_FIRST_URGENCY` (0.8), `_dodge_toward_safety()` removes the dodge component that points against `_zone_pull()`'s direction (a nearly opposite dodge becomes a perpendicular sidestep). With the switch off the old behavior returns: the dodge is also added before the pickup step, so it counts twice (6.0) and can outweigh the zone pull (at most 0.6 + 3.0·urgency = 3.6). `_shell_dodge` reads `cfg.weapons.mortar` with `.get()`.

## Situation telemetry (E009, `Bot.SITREP`, env `SURVIVOR_SITREP=0` disables)

`_sitrep()` runs at the end of `act()` inside a `try`; it reads the observation, the chosen `mode`/`target`/`fire`/`goal_label`, and `self.cover`, and writes only `self.sit`. A `SITREP {...}` JSON line goes to stderr every 30 s of match time and at interpreter exit (after `LAST`). `CONFIG zone` and `CONFIG drops` print once at start. Keys are documented in the `_sitrep` docstring; `tests/test_sitrep.py` proves actions are identical with it off.

Analysis tools (all synthetic): `scripts/zone_margin_probe.py` (does `_urgency` start before the latest straight-walk departure?), `scripts/zone_arbitration_probe.py` (does combat arbitration point away from safety outside the circle?), `scripts/lane_probe.py` (bot sight check vs exact muzzle geometry), `scripts/summarize_sim.py` (paired summary of an `exchange_sim.py --json` file).

## Files

- `bot.py`: per-tick policy. Remembers observations, chooses targets/modes, selects movement and weapons, handles loot/upgrades, and performs local steering.
- `nav.py`: 4 m walkability grid, approximate 3D LOS, directional clearance, and an incremental Dijkstra field toward the next zone.
- `mechfight.py`: official JSON-lines protocol wrapper. Owns stdin/stdout, latest-observation selection, exception fallback, and debug drawing.
- `Dockerfile`: pinned Python/NumPy runtime and bot entry point.

## Current decision flow

0. `Bot.act()` runs `clean_obs()` (fill missing lists, drop malformed entries, normalize weapon slots), feeds `Telemetry.observe()`, then calls `_act()`. Any exception is logged (rate-limited) and answered by `_fallback()`, a zone-seeking move that holds the current aim.
1. `_act()` → `_remember()` records visible actors, damage, failed pickups, and sounds.
2. `_update_field()` maintains zone navigation; `_urgency()` estimates rotation pressure.
3. `_decide()` builds `threats` with `_threats()` (visible mechs aiming at us within 150 m, plus mechs that hit us in the last 4 s, placed from memory or the hit bearing), updates the `recovering` latch (enter < 35% HP, exit ≥ 70%, off when alive ≤ 2), and selects a target and mode: retreat while recovering (unless a lone weaker enemy) or when outnumbered (≥ 2 threats and `hp_r < 0.6 × Σ threat health`), otherwise the original fight/avoid/farm rules.
4. `_zone_pull()` (with `_camp()` limited to walls 6 m inside the next circle), `_pick_goal()` (skipped while recovering), `_strafe()`, and retreat movement (away from all threats via `_away()`, with a `_jink()` sidestep) produce a desired vector. While recovering with no visible enemy, the bot moves away from out-of-view attackers.
5. `_weapons()` chooses aim and fire flags. Direct fire is allowed by `_has_shot()`: actors in this tick's `obs["mechs"]` beyond `vision.near_range` are trusted; closer or remembered actors need grid LOS. Grid LOS still picks the mortar arc. `_take()` and `_choose_upgrade()` handle loot.
6. Evasion: every visible mech charging a laser at us adds a sidestep; `_shell_dodge()` moves away from mortar impacts predicted within 2.5 s and splash + 3 m.
7. `_steer()` performs wall/zone-aware heading selection and stuck recovery.
8. Optional Dijkstra work consumes the remaining optional time budget.

## Extension points

- Use the stderr telemetry to tune or revert the threat, recovery and evasion constants with match evidence.
- Retreat currently breaks LOS only through `_steer()`'s per-heading hide bonus; choosing an explicit cover point is the next step if retreats keep failing.
- Generalize navigation only after measuring failures from direct-line goal movement.
- Keep approximate map LOS separate from the authoritative fact that a current observation contains an actor.

## Telemetry (stderr only)

- `CONFIG {...}` once at start: `vision`, `match`, `mech`, `weapons`, and zone phases from `hello.config`.
- `TELEMETRY {...}` every 60 s of match time: last state, damage taken/dealt by weapon, rear damage, kills, time outside the zone, mode tick counts, observation gaps, fallbacks, and `act()` latency (p50/p99 from 1 ms buckets, max, ticks ≥ 40 ms).
- `SUMMARY {...}` once at exit (atexit; SIGTERM exits cleanly): the same fields plus `death_window` (distinct attackers and damage by weapon in the 10 s before the last observation) and `end` (`place`, `kills`) captured by the read-only `StdinTap` around `sys.stdin`.
- `mechfight.py` is unchanged; the tap only reads lines it passes through.
- Tests: `tests/` (`python -m unittest discover -s tests`), including a subprocess JSON-lines round trip.

## Data boundaries

Current observations are authoritative for what is currently visible. `seen`, `attackers`, and `heard` are noisy memory with limited lifetimes. The map is approximate and must not override fresher direct observations. Runtime diagnostics go to stderr; stdout belongs to the protocol.
