# Experiment log

Use one row per meaningful behavioral iteration. Record neutral and negative results so they are not repeated.

## Baseline

- Team A initial submission: 22nd place on the practice leaderboard. A score of about 3.1 was mentioned but is **unverified**; no verified score is recorded.
- Baseline commit: `4bdcb01` (local tag `baseline-e000`; equals local `origin/main`, not re-fetched).
- Known-good submitted commit: presumed `4bdcb01` (only commit on `origin/main`); user to confirm.
- Candidate branch/commit: local branch `candidate` in `Mech-royale-bot/` (never pushed). `main` stays at the baseline.

## Validation commands (host: Windows, Python 3.14.5, NumPy 2.5.1; container uses Python 3.13 / NumPy 2.3.5)

- Compile: `python -m py_compile bot.py nav.py mechfight.py` (in `Mech-royale-bot/`)
- Unit: `python -m unittest discover -s tests` (in `Mech-royale-bot/`)
- Latency: `python scripts/bench_act.py [--bot-dir <dir>]` (workspace root; 2 km synthetic arena, 900 ticks, up to 15 visible mechs, 3 zone phases). Baseline copy: `git archive baseline-e000` extracted to `%TEMP%\mr-baseline-e000`.
- Official check and Docker build: NOT RUN so far. The kit (`check/check.py`, `arena/`, `protocol.md`) is not in the workspace and `docker` is not installed.
- Local match runner: none exists, so no candidate has placement evidence.

## Experiments

| ID | Date | Baseline/candidate commit | Hypothesis | Change | Validation | Matches/score | Decision |
|---|---|---|---|---|---|---|---|
| E000 | 2026-11-20 | Initial submission / `4bdcb01` (presumed) | Establish rollback baseline | Existing survivor bot | Official check result: `FILL IN` | 22nd; ~3.1 reported, unverified | Preserve baseline |
| E001 | 2026-11-20 | `4bdcb01` → `4def042` | A visible enemy without `max_hp` (optional in the schema) raises `KeyError` in `_decide` every tick; the wrapper then repeats the last move/aim and never fires. Other required-looking fields can fail the same way. | `bot.py`: `clean_obs()` fills missing lists and drops malformed entries; `hp_frac()` treats hidden HP as full; robust `__init__`, `_take`, `_choose_upgrade`, `_laser_on_me`; `act()` catches exceptions, logs at most 5 then every 100th, and returns a zone-seeking fallback. Tests: `tests/test_robustness.py` (10). | Compile PASS. Unit 10/10 PASS. Latency (host) baseline p50 0.08 / p95 20.22 / p99 20.46 / max 20.59 ms; candidate p50 0.14 / p95 20.20 / p99 20.44 / max 20.69 ms; 0 over 50 ms, 0 exceptions. Official check NOT RUN. Matches NOT RUN. | None | Keep (correctness). Expected neutral if the server always sends `max_hp`. |
| E002 | 2026-11-20 | `4def042` → `bfda399` | No match evidence exists, so loss mechanisms can't be ranked. Practice house bots don't fight each other, so practice points ≈ house bots that die (to us or the zone) before we do; we need kills, damage sources, zone time, and focus-fire counts per match. | `bot.py`: `Telemetry` (CONFIG / TELEMETRY every 60 s / SUMMARY at exit with `end` place/kills via read-only `StdinTap`), atexit + SIGTERM handler. No decision changes. Tests: `tests/test_telemetry.py` (5, incl. subprocess JSON-lines round trip with 40 obs). | Compile PASS. Unit 15/15 PASS. Latency (host) p50 0.15 / p95 20.23 / p99 20.46 / max 23.22 ms, 0 over 40 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep (instrumentation). Behavior unchanged; stderr ≈ 1 line/min + 2. |
| E003 | 2026-11-20 | `bfda399` → `89ac266` | Grid LOS false negatives (A001: 2.6–2.9% of clear lines, 8.6–10.3% wall-adjacent, synthetic) disable all direct fire at enemies the server reports as visible. In practice, points come from house bots dying before us, so lost damage costs points. `protocol.md` unavailable; relies on the schema's "visible mechs" wording. | `bot.py`: `_has_shot()` trusts actors in this tick's `obs["mechs"]` beyond `vision.near_range`; used for direct-fire gating, lead aim, mortar aim arbitration, "weaker enemy" fights and drone farming. Mortar arc still uses grid LOS; near-range/remembered actors unchanged. Tests: `tests/test_firing.py` (6; 4 fail on baseline, 2 preserved-behavior tests pass on both). | Compile PASS. Unit 21/21 PASS. Latency (host) p50 0.14 / p95 20.16 / p99 20.42 / max 21.05 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep (weapon correctness). Risk: wasted gun ammo if `near_range` semantics differ from assumed. |
| E004 | 2026-11-20 | `89ac266` → `e504e7d` | Fight selection looks at one target: 3 enemies within 60 m at full HP → `fight` (A001). Against a vision-sharing squad or an FFA third party, trading with 2+ shooters ends the life early, which ends point accumulation. Backing straight away from shooters is also easy to lead. | `bot.py`: `_threats()` (visible mechs aiming within 0.35 rad and 150 m, plus mech hits in last 4 s, placed from memory or hit bearing; NPC/zone hits excluded), `_away()`; in `_decide`, retreat when ≥2 threats, alive > 2, and `hp_r < 0.6 × Σ threat hp`. Retreat moves away from all threats with a 0.9-weight sidestep that flips every 0.8–2.2 s (`_jink`, shared with `_strafe`). Tests: `tests/test_threats.py` (10). | Compile PASS. Unit 31/31 PASS. Latency (host) p50 0.15 / p95 20.22 / p99 20.47 / max 22.21 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep pending match evidence. Risks: fewer kills if retreat is too frequent in practice; sidestep slows disengagement by ~13%; constants 150 m / 0.35 rad / 0.6 are unmeasured. |
| E005 | 2026-11-20 | `e504e7d` → `871d979` | Retreat ends at 35% HP with no hysteresis (A001: 34% → retreat, 36% → fight), and once the attacker is out of view the bot resumes loot, crates and drone farming. Regeneration needs a quiet period (5 s per the `bot.py` docstring; not verified against `protocol.md`), so re-engaging at ~36% likely dies. | `bot.py`: `recovering` latch (enter < 0.35, exit ≥ 0.70, never when alive ≤ 2). While set: retreat from visible enemies unless a single, weaker one; no `_pick_goal`, no crate shots, no drone farm/fight; if hit from out of view, move away from the attacker estimate with boost and break LOS via `_steer` hide point. Tests: `tests/test_recovery.py` (6). | Compile PASS. Unit 37/37 PASS. Latency (host) 2 km: p50 0.16 / p95 20.30 / p99 20.51 / max 23.68 ms; 1 km: p50 0.14 / p95 0.21 / p99 20.11 / max 20.36 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep pending match evidence. Risk: passive between 35% and 70% HP gives fewer practice kills; exit threshold unmeasured. |
| E006 | 2026-11-20 | `871d979` → `6f65505` | Zone-safety audit: `_urgency` is 0 inside the next circle's seed radius (60% of radius, or 50%/≥ 6 m when radius ≤ 40 m), and `_camp` then drifts to the nearest wall within 22 m without checking that the wall is inside the next circle. In small late circles that walks the bot out of the circle that is about to close. | `bot.py`: `_camp` only accepts a wall whose foot is `CAMP_MARGIN` = 6 m inside `next_radius`; otherwise stay still. Tests: `tests/test_zone.py` (4; 3 fail on `871d979`, 1 large-circle preserved-behavior test passes on both). | Compile PASS. Unit 41/41 PASS. Latency (host) p50 0.19 / p95 20.29 / p99 20.58 / max 23.16 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep (zone correctness). Small expected effect; only small circles change. |
| E007 | 2026-11-20 | `6f65505` → `b1ac576` | `obs.projectiles` and `obs.beams` were never read; the laser telegraph sidestep covered only the selected target. Predictable damage (mortar splash, charged lasers from a second enemy) is avoidable. | `bot.py`: `_shell_dodge()` predicts each mortar shell's impact (gravity from `config.weapons.mortar.gravity`, ground = our height) and, if it lands within 2.5 s and splash + 3 m (splash key guessed: `splash`/`splash_radius`/`blast_radius`/`radius`, default 7 m), moves away (weight 3 under 1.2 s, else 2) and boosts. Laser sidestep loops over all visible mechs. CONFIG line now includes `weapons` so the real key names show up in stderr. Tests: `tests/test_evasion.py` (6). | Compile PASS. Unit 47/47 PASS. Latency (host) p50 0.18 / p95 20.29 / p99 20.50 / max 22.45 ms. Official check NOT RUN. Matches NOT RUN. | None | Keep pending match evidence. Risks: wrong splash key → 7 m default; flat-ground impact estimate on slopes/roofs; dodge can briefly fight the zone pull. |
| E002b | 2026-11-20 | `b1ac576` → `7ed901b` | Replaying a whole match through stdin at once showed SUMMARY `end` = null: the wrapper's reader thread can see `end` before the bot is constructed. | `bot.py`: `StdinTap` stores `end` in module state; `_final_summary` attaches it. Tests: +1 in `tests/test_telemetry.py`. Tools: `scripts/summarize_stderr.py` (aggregates SUMMARY lines: placement points mean/sd/se, distribution, damage by source, death-window attackers, zone time, latency, fallbacks, mode share), `scripts/fake_match_stderr.py` (paced scripted matches to exercise the pipeline; not a simulator). | Compile PASS. Unit 48/48 PASS. Pipeline run on 3 fake matches PASS (SUMMARY parsed in 3/3; values are fake). Official check NOT RUN. | None | Keep (instrumentation fix). |
| E008 | 2026-11-21 | workspace `bot.py` SHA-256 `97bce237…` (lineage of the navigation patch, not E001–E007; git not readable from the agent session) → uncommitted working tree | Losing exchanges end lives: at ≥ 50% HP the bot fights any attacker however the trade goes; with the attacker out of `obs.mechs` it makes no defensive move; retreat walks straight away in the open. Breaking line of sight to reach the 5 s regeneration delay should lengthen those lives. | `bot.py`: cover episode (`_cover_update`, `_find_cover`, `_hidden`, `_cover_zone_ok`, `_cover_move`, `_end_cover`), `COVER*` knobs, `SURVIVOR_COVER` env switch, `COVER`/`LAST` stderr lines. Tests: `tests/test_cover.py` (20 incl. JSON-lines round trip). Tools: `scripts/exchange_sim.py`, `scripts/bench_cover.py`, `scripts/compare_runs.py`; `scripts/bench_act.py` fixed (config lacked `weapons`, so every baseline `act()` raised and timings were meaningless). | Compile PASS. `test_cover` 20/20 PASS; full suite 70 run, 43 fail/error = the same 43 as baseline. Host latency PASS (see E008 details). Harness: +2.52 s mean life per losing exchange (SE 0.44, 200 fresh pairs). Docker build / official check NOT RUN. Real matches NOT RUN. | None (no runner) | Keep behind the switch; submit only after a paired runner comparison (see E008 details). |

## Candidate status (2026-11-20)

- Candidate: `7ed901b` on local branch `candidate` (E001–E007 + E002b). `main` and tag `baseline-e000` stay at `4bdcb01`. Nothing pushed.
- Diff vs baseline: `bot.py` (+483/−36 before E002b) and `tests/`. `Dockerfile`, `mechfight.py`, `nav.py` unchanged; no new dependencies.
- Latency (host, `scripts/bench_act.py --ticks 6000`, 2 km arena): candidate `7ed901b` p50 0.18 / p95 0.25 / p99 20.15 / max 21.32 ms; baseline p50 0.08 / p95 0.11 / p99 20.13 / max 20.75 ms. The ~20 ms ticks are the intentional Dijkstra budget. 0 ticks over 40 ms for either. Not measured in the 1-CPU container.
- Not submission-ready: official Docker `check/check.py` NOT RUN (kit and Docker unavailable here); no match evidence; behavior constants in E004–E007 unmeasured.
- Lower-risk subset if a staged submission is preferred: `89ac266` (E001–E003: robustness, telemetry, fire gating), but it lacks E002b's `end` fix.

## Analyses

| ID | Date | Commit | Artifacts | Findings | Next |
|---|---|---|---|---|---|
| A001 | 2026-11-20 | `4bdcb01` (local `origin/main`, not re-fetched; confirm it is the submitted commit) | No replays, stderr, per-match places, `protocol.md`, or kit in the workspace. Synthetic probes only: `python scripts/probe_baseline.py` (1 km and `--size 2048 --buildings 400 --seed 8`). | No match evidence, so no loss mechanism is measured. Code behavior on synthetic input: `act()` raises `KeyError: 'max_hp'` when an enemy omits `max_hp` (schema marks it optional); grid LOS blocks all direct fire on 2.6–2.9% of exactly clear lines (n=2185, 2505) and 8.6–10.3% when the shooter is wall-adjacent (n=39, 35); 3 enemies within 60 m at full HP gives `fight`; retreat flips to `fight` between 34% and 36% HP; mid-urgency retreat moves away from the next circle. Latency on Windows dev PC: decision-only p99 ≤ 0.4 ms, Field ticks max 34.7 ms on a 2 km arena, 0 over 50 ms. | Pull the baseline's practice stderr and placements; search for `act() failed`. |

## Navigation patch

- Base: `833447c`. Hypothesis: earlier zone rotation and clear-heading recovery reduce time spent outside the zone and stuck near obstacles.
- Official checker on Windows: 300/300 on time, p99 20.6 ms, max 21.0 ms. Both focused navigation tests passed. The full suite reported 6 failures and 35 errors, matching the baseline counts.
- Docker conformance and placement remain unmeasured.

## E008 details: break a losing exchange (2026-11-21)

**Reference.** Official best practice score 3.38 (user-reported; mean place ≈ 12.62). The commit behind it is not recorded here. Local baseline = workspace `bot.py` before E008 (SHA-256 `97BCE23787D380B71C128F81D69C10D353A3E487604EE1F46E26B152CAA69E3A`). No local number below is comparable to 3.38.

**Evidence available.** No replays, match stderr, kit (`protocol.md`, `check/check.py`, `arena/`), Docker or match runner on this machine. The 10 PM exhibition notes (free-for-all, Team A absent) suggested the hypothesis: AT regained ≈ 420 armor in 20 s beside a long wall with zero damage dealt, and lost ≈ 880 armor in 11 s in a narrow lane without offensive progress. They don't show what AT sensed or why.

**Code-derived failure (verified by reading `_decide`/`act`).** (1) `attacking or d < 60` → `fight` at any HP ≥ 50%, whatever the exchange; (2) attacker missing from `obs.mechs` → `_decide` returns `None` → camp/loot movement while being hit; (3) `retreat` = move directly away with a +1.0 per-heading hide bonus, no destination that breaks LOS.

**Harness.** `scripts/exchange_sim.py`: the real `Bot.act()` against 1–3 scripted hunters (shared knowledge of our position every 3 s, exact box LOS, 120° FOV, assumed regen 45 HP/s after 5 s) on random 512 m box-building arenas, 45 s per episode. Scenarios: `outgunned1` (1 accurate shooter), `squad3` (3 hunters), `winning1` (control: we out-damage one enemy), `rotate2` (2 hunters during a zone move). It measures time alive and exposure under invented rules, not placement. Reproduce: `python scripts/exchange_sim.py --variant base=bot.py:COVER=0 --variant cover=bot.py --seeds 200-249` (about 3 minutes for two variants on this host).

**A002, baseline classification (harness, seeds 200–249).** 108 deaths. In the last 5 s: 72% of ticks in `retreat`, 78% exposed to a shooter, 16% firing, 52 m moved, HP 0.23 at −5 s; 103/108 died within 10 s of entering retreat. Pattern: retreating in the open until caught. Exposure-grid check: `_hidden` says hidden for 3,786 random spot/shooter pairs; exact geometry sees 7 of them (0.2%).

**Development (seeds 0–19).** First version: 97 of 110 episodes ended `exposed` within ~1 s (spots hidden from the shooter's current position only). Added a ±10 m flank margin and re-pick on hits at the spot; `COVER_RETRY_S` 4 → 1 s after a failed spot (paired +2.69 → +3.34 s on dev seeds). Held-out seeds 100–149 then exposed a stall bug (outgunned1/135: 5 s stuck against a wall, re-picking the same heading), fixed by excluding the stalled heading and ending after a second stall. Final comparison on unused seeds 200–249.

| Harness, 50 seeds × scenario | base: survived / mean life | cover: survived / mean life | paired Δ life (SE) | better/tied/worse | survival gained/lost | Δ damage taken |
|---|---|---|---:|---|---|---:|
| outgunned1 | 34 / 42.6 s | 43 / 44.7 s | +2.10 s (0.69) | 15/29/6 | 15/6 | −11.7% max HP |
| squad3 | 0 / 22.5 s | 0 / 27.0 s | +4.53 s (1.19) | 30/10/10 | 0/0 | +0.5% |
| winning1 (control) | 50 / 45.0 s | 50 / 45.0 s | 0.00 | 0/50/0 | 0/0 | 0 (0 cover starts, kills 43 = 43) |
| rotate2 | 8 / 33.4 s | 13 / 36.9 s | +3.45 s (0.98) | 30/11/9 | 6/1 | −3.0% |
| all 200 pairs | 92 / 35.9 s | 106 / 38.4 s | +2.52 s (0.44) | 75/100/25 | 21/7 | −3.6% |

- Switch check: `COVER=0` vs the untouched baseline file: 199/200 identical; the one differing `rotate2` pair comes from time-budgeted zone-Field growth (seeds 100–149: 196/200 identical). That is the run-to-run noise floor.
- Pre-fix version on seeds 100–149: +2.33 s (SE 0.39), 87/91/22, survival 28/3.
- Diagnostics (cover, 200–249): 358 episodes; ends `exposed` 327, `stuck` 17, `healed` 13, `zone` 1. HP 0.51 at start (mean over all episodes, including re-tries), 0.52 on first arrival after 1.2 s (n 318); 40 never arrived. Deaths within 10 s of entering retreat/cover: 91/94 (base 103/108). Zone damage 0 in every run, every variant.
- Worst regressions: rotate2/225 45.0 → 32.3 s, squad3/202 36.9 → 25.3 s, squad3/213 31.6 → 22.0 s. In the traced case (squad3/130, pre-fix) a spot hidden from the known shooters was seen by another squad member en route; no full-cover spot existed afterwards and the bot fell back to open-ground retreat from a worse position. Five of the six lost `outgunned1` survivals died at 42–44.6 s of the 45 s window.

**Latency (Windows host, Python 3.14.5, NumPy 2.5.1; not the 1-CPU container).**

- `python scripts/bench_act.py --ticks 6000 --arena-dir %TEMP%\mr_bench_act_arena`. Candidate: p50 0.20, p95 0.54, p99 20.25, max 38.16 ms, 0 ticks > 40 ms. Baseline: p50 0.21, p95 0.51, p99 20.25, max 39.69 ms, 0 > 40 ms. An earlier pair on the same host gave candidate max 44.72 and baseline max 42.03 ms, one tick > 40 ms each, so the host is noisy. Use `--arena-dir` where temp dirs can't be deleted.
- `python scripts/bench_cover.py --trials 400`: trigger ticks p99 21.05, max 22.14 ms; `_find_cover` alone p50 3.55, p95 6.16, p99 7.71, max 10.15 ms. Same-tick zone change: the search defers (0 starts). Those ticks still reach p99 48.6, max 67.0 ms because `nav.Field.__init__` is unbudgeted. The baseline shows the same spike (p99 50.3–61.7, max 52.5–66.9 ms across runs). This is a pre-existing deadline risk on large next circles.

**Status.** Compile PASS. `tests/test_cover.py` 20/20 PASS. Full suite: 70 run, 43 fail/error, the same 43 as baseline (older tests target the E001–E007 lineage). Docker build NOT RUN, official `check/check.py` NOT RUN, real or practice matches NOT RUN. **Not submission-ready.**

**Exact comparison to run when a runner is available.** Build one image and toggle with the env var, so both arms run identical code. If the scorer can't pass env vars, use two images, one built from a copy with `COVER = False`.

```powershell
docker build -t survivor:e008 .
# per match key (same seed, slot, opponents): run both arms
docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -e SURVIVOR_COVER=0 -v "<arena>:/arena:ro" survivor:e008 2> base\<match>.txt
docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -e SURVIVOR_COVER=1 -v "<arena>:/arena:ro" survivor:e008 2> cover\<match>.txt
# runs.csv: match,variant,place,stderr   (variant base|cover)
python scripts/compare_runs.py runs.csv --ref base --cand cover
```

Use ≥ 48 paired practice matches (one practice set). Keep the candidate only if mean placement points improve with the paired SE well below the gain. Also check that eliminations within 10 s of a cover start fall and that no zone deaths appear; inspect the worst five regressions. Check the official checker and container latency first.

**Next experiment (one at a time).** `exposed` ends 91% of episodes and `squad3` survival stays 0/50: when no spot hides from every shooter, move to the reachable spot visible to the fewest of them (partial cover). Separately, budget `Field.__init__` (pre-existing > 50 ms ticks on large circles).

## Recording rules

- Use mean placement points and placement distribution as primary metrics; rank alone is not a controlled comparison.
- Record match count, seed conditions when available, and whether opponents were practice house bots or other players.
- Include official-check status and p95/p99 action latency for candidates.
- Mark unsupported outcomes as inconclusive rather than improvements.
- Link the exact commit used for every submitted score and keep that commit publicly fetchable.
