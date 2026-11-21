# Current improvement backlog

These are code-review hypotheses, not measured rankings. Use match evidence to reprioritize and remove items after fixes. Status notes refer to `docs/EXPERIMENTS.md`; "patched" means implemented and unit-tested on the local `candidate` branch, not proven in matches.

## P0: correctness and survival

1. **Visible-target suppression:** patched in E003 (`_has_shot`). Verify with match telemetry (`dealt`) that direct fire did not drop.
2. **Zone/combat vector conflict:** still open. High urgency adds boost, and the zone pull usually outweighs combat vectors, but retreat (1.6) can beat the pull at low/mid urgency. E006 fixed only the `_camp` case. Add an explicit safety override if telemetry shows `out_zone_s` or `taken.zone` > 0.
3. **Single-target threat model:** patched in E004 (`_threats`, outnumbered retreat). Beams, sounds and projectiles are not yet part of the threat set.
4. **Automatic close-range fights:** partly open. A single enemy within 60 m still triggers `fight`; only 2+ threats now retreat.
5. **Weak retreat/regeneration:** partly patched in E005 (recovery latch to 70%). Retreat still has no explicit cover target; LOS breaking relies on `_steer`'s hide bonus.
6. **Exceptions on optional fields:** patched in E001 (`clean_obs`, `hp_frac`, zone-seeking fallback).

## P1: avoidable damage and positioning

- Mortar impact evasion and every-enemy laser telegraph: patched in E007. Gun rounds and NPC projectiles are not dodged.
- Score camping and retreat positions by threat cover, future-zone safety, and escape routes rather than wall proximity alone.
- Arbitrate weapon selection and shared aim explicitly; preserve energy needed for lightning or emergency boost. (Gun + mortar without a laser: the mortar takes the aim whenever it is ready.)
- Make supply drops and loot path-, threat-, and zone-aware rather than attractive solely by distance and item rank.

## P2: later work

- Generalize navigation to arbitrary cover and loot goals.
- Make upgrade valuation sensitive to current equipment and survival needs.
- Consider rooftops only after measuring a benefit in repeatable tests.

## Useful measurements

Track placement distribution, elimination time, damage by source, zone damage/deaths, time spent outside the zone, retreat success, regeneration uptime, action latency, exceptions, and same-seed baseline comparisons. Do not use kills as the primary success metric. Since E002 the bot writes most of these to stderr as `TELEMETRY`/`SUMMARY` JSON lines.

Practice note: house bots don't fight each other, so practice placement points equal the number of house bots that die (to us, the zone, or NPCs) before we do. Survival without kills or zone deaths among house bots scores nothing in practice; FFA qualifiers reward survival directly.
