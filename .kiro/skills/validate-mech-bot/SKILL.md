---
name: validate-mech-bot
description: Validate, test, benchmark, Docker-build, or prepare a Mech Royale bot candidate for submission. Use after code changes, before submitting, and when checking protocol compliance or the 50 ms action budget.
---

# Validate a candidate

Run checks from the repository root. Discover exact local paths from `README.md` and the official kit rather than assuming them.

## Required gates

1. **Static:** compile `bot.py`, `nav.py`, and `mechfight.py`; inspect the diff for accidental protocol or dependency changes.
2. **Unit:** run deterministic tests covering changed decisions and regression cases.
3. **Official check:** build the Docker image and run `check/check.py` under the documented CPU, memory, user, network, filesystem, and `/arena` constraints.
4. **Performance:** benchmark `act()` on representative worst-case observations. Report environment, iterations, p50/p95/p99/max, exceptions, and skipped observations if measurable. Average latency alone is insufficient.
5. **Behavioral:** if a local match runner exists, compare baseline and candidate over multiple identical seeds. Record placement, damage sources, zone failures, and timeouts.

## Submission gate

Confirm the build is reproducible, dependencies are pinned, the repo is public at the intended commit, the submitted tree is below 20 MB, and the known-best entry remains recoverable. Do not push or submit unless the user explicitly asks.

## Reporting

List each check as PASS, FAIL, or NOT RUN with the exact command and observed result. A NOT RUN official check means the candidate is not submission-ready. Never replace missing measurements with estimates presented as facts.
