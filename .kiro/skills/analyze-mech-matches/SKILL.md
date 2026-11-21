---
name: analyze-mech-matches
description: Analyze Mech Royale replays, match logs, stderr, placement results, deaths, zone damage, focus fire, latency, or baseline-versus-candidate performance. Use before prioritizing bot changes and after practice runs.
---

# Analyze match evidence

1. Identify available artifacts and their format. Read the official protocol or replay documentation before inventing a parser.
2. Establish the baseline: commit, candidate, seed set if available, match count, mean placement points, and placement distribution.
3. Classify lost placement by evidence: zone, focus fire, failed escape, exposed stationary play, projectile damage, navigation stall, exception, timeout, or unknown.
4. Quantify frequencies and show representative timestamps/ticks. Preserve contradictory evidence and use `unknown` rather than guessing.
5. Compare baseline and candidate on identical conditions when possible. Report sample size and variance; do not overinterpret one match.
6. Recommend one next experiment tied to the largest evidenced loss mechanism.

## Output format

| Area | Evidence | Frequency/sample | Likely mechanism | Confidence | Next experiment |
|---|---|---:|---|---|---|

End with artifacts examined, artifacts unavailable, and any parsing limitations. Never infer exact death causes or score gains from placement alone.
