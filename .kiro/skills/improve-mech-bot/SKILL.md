---
name: improve-mech-bot
description: Improve or modify the Mech Royale bot's strategy, survival, navigation, combat, looting, targeting, zone rotation, upgrades, or endgame behavior. Use for bot.py or nav.py behavior changes and leaderboard-improvement iterations.
---

# Improve the bot

## Procedure

1. Read `docs/ARCHITECTURE.md`, `docs/EXPERIMENTS.md`, `references/improvement-backlog.md`, the official protocol, and relevant source.
2. Check available match evidence. Separate observed failure modes from code-derived hypotheses.
3. Write a short experiment statement: baseline, failure mechanism, proposed change, expected placement effect, and regression risks.
4. Choose the smallest coherent patch that tests the hypothesis. Preserve unrelated behavior and the known-good baseline.
5. Add or update deterministic tests for the changed decision logic.
6. Follow the `validate-mech-bot` skill. Do not stop at syntax checking when behavioral or runtime checks are feasible.
7. Update architecture notes if responsibilities changed and append the experiment outcome, including negative results.

## Strategy guardrails

- Optimize placement, not aggression or kill count.
- Fix survival correctness before tuning distances, HP thresholds, or upgrade order.
- Evaluate all relevant threats rather than assuming the selected target is the only attacker.
- Make zone urgency capable of overriding combat and loot movement.
- Retreat should reach safe cover, break LOS, and persist long enough for regeneration; merely backing away is insufficient.
- Keep practice-squad counterplay compatible with free-for-all finals.
- Avoid complex rooftop behavior or new dependencies until core survival fixes are validated.

## Output

Summarize the hypothesis, code changes, tests actually run, latency results, and remaining uncertainty. Do not promise a leaderboard gain.
