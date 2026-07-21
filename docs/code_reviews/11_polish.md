# Code review — step 11 (polish, docs, and final review)

Scope: `README.md` rewrite, `docs/verification.md`, `docs/building.md`, `docs/backlog.md`,
coverage floors in `pyproject.toml` + CI, and the three missing `Config` fields added to
`teamgym-watcher.example.toml` with a drift-guard test. No `mapping.py`/`hierarchy.py`/
`matcher.py` changes in this step, so Lua parity is not directly in scope here (it was
already covered in earlier per-module reviews); spot-checked anyway where the README makes
specific factual claims about hierarchy behaviour (see below).

## Verification performed

- `uv run ruff check .` — clean.
- `uv run ruff format --check .` — clean (34 files).
- `uv run mypy src/` — clean (15 files).
- `uv run pytest -q --cov=teamgym_watcher.{mapping,hierarchy,matcher} --cov-fail-under=90` —
  passes (all three modules already at 100%).
- `uv run pytest -q --cov=teamgym_watcher --cov-fail-under=85` — passes (93.70% actual; only
  `watcher.py` at 68% pulls the average down, driven by `Watcher.start`'s background-thread
  path, which is legitimately hard to unit-test and already covered end-to-end in
  `tests/integration/`).
- `git status` — clean, nothing left uncommitted.
- No `TODO`/`FIXME`/`XXX` left in `src/` or `tests/`.
- Cross-checked every factual claim the new README/docs make against the actual source and
  the sibling `teamgym-sorter`/`lightroom-plugins` repos rather than taking them on faith:
  - `teamgym-watcher.example.toml` now documents all 13 `Config` fields (verified against
    the dataclass in `src/teamgym_watcher/config.py`); the new
    `TestExampleToml.test_every_config_field_is_documented`/`test_parses_and_loads_with_real_dirs`
    tests actually exercise the file via `tomllib` + `load_config`, not just string matching.
  - JWT "expires after 1 hour" and the 5-minute early-expiry buffer: confirmed against
    `../teamgym-sorter/docs/api/README.md` and `auth.py`'s `EARLY_EXPIRY_BUFFER_SECONDS`.
  - Timezone resolution priority order (photo offset → `Config.timezone` → machine local):
    confirmed against `exif.py`'s docstring and `_resolve_timezone` logic.
  - `recursive`/`exiftool_enabled`/`exiftool_path` correctly documented as TOML-only (no CLI
    flag) — confirmed against `cli.py`'s option list and `_build_overrides`.
  - `PodiumTraining` → "Podium Training" display-name substitution and the `"ClubName -
    TeamName"` / `"OlympicCode - ClubName"` composite segment formats: confirmed against
    `hierarchy.py` and `../lightroom-plugins/teamgym.lrdevplugin/PhotoCollectionHierarchy.lua`.
  - `docs/building.md`'s references to `scripts/run_build.ps1`/`.sh` and
    `prompts/tasks/code_review.md`: files exist as described.

## Findings

None. No must-fix issues — this step is docs/config/CI only, all claims verified accurate,
all quality gates green. No fix commit needed.

## Minor (non-blocking) observation

CI now runs `pytest` three times per OS (`Test`, `Coverage (parity-critical modules)`,
`Coverage (overall)`) instead of one combined coverage run with two `--cov-fail-under`
thresholds merged into a single invocation. `coverage.py` has no native per-module
`fail-under`, so the split is a reasonable and correct way to get two floors, just a bit
slower in CI (3x matrix × 3 OSes). Not worth blocking on; noted for a future pass if CI time
becomes a problem.
