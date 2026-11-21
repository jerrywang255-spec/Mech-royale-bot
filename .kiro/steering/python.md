---
inclusion: fileMatch
fileMatchPattern: ["**/*.py", "Dockerfile"]
---

# Bot code conventions

- Prefer standard-library Python plus the existing pinned NumPy dependency. Do not add frameworks for simple decision logic.
- Keep per-tick loops bounded. Move expensive map work to initialization or incremental budgeted work, and benchmark worst-case observations.
- Use `hello.seed` for reproducible randomness and `hello.config` plus weapon stats instead of duplicating configurable mechanics as constants.
- Read optional protocol fields defensively with `.get()`. Never allow malformed or incomplete observations to crash the main loop.
- Actors in the current `obs["mechs"]` list are currently observed. Do not override that fact with a coarser approximate map LOS result.
- Keep movement, aiming, and firing decisions consistent within a tick. A shared aim must not accidentally activate a different weapon's firing condition.
- Write protocol output only through the wrapper. Use stderr for diagnostics and keep hot-path logging minimal.
- Add deterministic `unittest` coverage for pure decision functions where practical. Use small synthetic observations and avoid requiring private seeds.
- Update `docs/ARCHITECTURE.md` when changing module responsibilities or major state flows.
