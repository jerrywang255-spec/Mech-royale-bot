# Bot architecture

Update this file when module responsibilities or major decision flows change.

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
