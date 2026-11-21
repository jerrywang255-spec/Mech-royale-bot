# Mech Royale working agreement

## Mission

Improve Team A's placement score with reliable, evidence-driven iterations. Placement is the objective; kills and loot matter only when they improve survival or endgame strength.

## Non-negotiables

- Read `protocol.md` before changing protocol, observation, weapon, zone, or upgrade behavior.
- Keep stdout reserved for protocol JSON. Log diagnostics to stderr only.
- Stay safely below the 50 ms action deadline on 1 CPU. Keep optional work incremental and bounded.
- Respect the 512 MB memory limit, no runtime network, read-only filesystem, and writable `/tmp` only.
- Keep dependencies minimal and pinned. Preserve the Docker build and map-failure fallback.
- Avoid changing `mechfight.py` unless a protocol bug requires it; explain any such change.
- Never push, submit, delete history, or overwrite the known-best submission unless explicitly asked.
- Never claim a test ran or a score improved without actual evidence.

## Iteration workflow

1. Read `docs/ARCHITECTURE.md`, `docs/EXPERIMENTS.md`, and the relevant code.
2. Inspect available replays/logs and identify the largest observed source of lost placement.
3. State one hypothesis, the intended behavioral change, and the metric it should affect.
4. Make the smallest coherent patch. Avoid unrelated rewrites and constant-only tuning without evidence.
5. Add focused tests for decision logic and run the validation workflow.
6. Report changed files, actual command results, runtime measurements, and remaining risks.
7. Update the experiment log with the result, including neutral and negative outcomes.

## Decision priorities

Prefer fixes in this order: crashes/timeouts, zone deaths, unavoidable focus fire, failed retreat/regeneration, unsafe positioning, weapon correctness, loot efficiency, then minor tuning. Preserve endgame competitiveness; do not optimize only for passive practice survival.
