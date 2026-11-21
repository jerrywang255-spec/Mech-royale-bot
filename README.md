# Survivor: a Mech Royale bot

A placement-first bot for the Builders Buildathon **Mech Royale** track. It reads one observation every
0.1 s and answers with where to move, aim and fire (protocol in the kit's `protocol.md`).

## How it plays

| Priority | What it does |
| --- | --- |
| 1. Zone | Builds a walking map of the campus at startup and follows the shortest route around buildings into the *next* circle well before it shrinks. Boosts when time is short. |
| 2. Pick fights carefully | Fights back when shot, when an enemy is within 60 m, when only 3 mechs are left, or when the enemy is clearly weaker. Otherwise it backs away and keeps facing them (rear hits do 1.5x damage). |
| 3. Retreat to heal | Below 35% HP it backs off toward headings that put a building between it and the attacker, so health regenerates. |
| 4. Shoot well | Leads gun shots on moving targets; charges the laser and fires only when lined up; fires lightning inside its 45 m cone; lobs mortar shells, even over buildings. Sidesteps a laser that is aimed at it. |
| 5. Loot | Picks up laser > lightning > mortar, grabs cubes and supply drops when it's quiet, shoots crates, and picks armor and HP upgrades first. |

## Files

| File | What it is |
| --- | --- |
| `Dockerfile` | Builds the bot image (pinned Python and numpy). The scorer builds this. |
| `bot.py` | All decisions: zone, fight/avoid/retreat, aiming, weapons, loot, upgrades. |
| `nav.py` | The map: walk grid, line-of-sight checks, and the distance-to-safety field. |
| `mechfight.py` | The kit's protocol wrapper (unchanged). |

## Test it on your computer (Windows)

1. Install **Docker Desktop** (free for personal and education use) and start it.
2. Put the kit's `check` and `arena` folders next to this folder, then in PowerShell from this folder:

```powershell
docker build -t survivor .
python ..\mechfight-kit\check\check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "${PWD}\..\mechfight-kit\arena:/arena:ro" survivor
```

It should end with `PASS`. This is the same check the scorer runs before your matches.

## Submit

1. Create a **public** GitHub repository (for example `mech-royale-bot`).
2. Upload `Dockerfile`, `bot.py`, `nav.py`, `mechfight.py` and this README to the top level of it.
3. On the Mech Royale track page, paste the repository URL and press **Submit**. Leave the commit box empty.
4. Keep the repository public, with every submitted commit, until results are published.

## Ideas to improve it

- Tune the numbers at the top of `_decide` in `bot.py` (retreat at 35% HP, fight within 60 m).
- Rooftops: jet onto a roof inside the zone late in the match for height and cover.
- Track enemies from sounds (`obs.sounds`) to know where fights are and avoid them.
- Dodge mortar shells using `obs.projectiles` and their gravity.
