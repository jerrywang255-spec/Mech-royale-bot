---
inclusion: always
---

# Mech Royale project context

- Score is the mean of `16 - place`; a win earns 15 and first elimination earns 0.
- Practice uses 48 matches against 15 house bots acting as one squad. They share vision, do not fight each other, and increasingly hunt the player, especially when weak.
- Qualifiers and finals use free-for-alls against other bots on new seeds. Avoid logic that depends on house-bot identity, fixed spawns, or memorized seeds.
- The bot receives observations at 10 Hz and must answer within 50 ms. Late answers lose control and firing opportunity.
- Runtime: linux/amd64, 1 CPU, 512 MB RAM, no network, unprivileged user, read-only filesystem except `/tmp`, map mounted at `/arena`.
- Keep submitted dependencies pinned and the repository buildable. A crash leaves a stationary scored target; robust fallback behavior is essential.
- Submission opportunities are limited. Validate locally and compare candidates before spending a submission.
- Treat the current best leaderboard entry as the rollback baseline. Never replace it with an untested candidate.
