# Python rules

- Python ≥ 3.12. `uv` + `pyproject.toml` only (no setup.py/requirements.txt).
- Layout: `src/teamgym_watcher/`, tests in `tests/`. Entry point `teamgym-watcher` via
  `[project.scripts]`.
- Fully typed: `mypy --strict` clean on `src/`. Use `dataclasses` (frozen where sensible) or
  plain classes; no dict-shaped data passed around — define types for Tag, PhotoMetadata,
  MatchResult, etc.
- `ruff` for lint AND format (line length 100); config in `pyproject.toml`. Rule set: at least
  `E,F,W,I,UP,B,SIM,PTH,RUF`. Zero warnings.
- `pathlib.Path` everywhere; never string paths, never `os.path`.
- Timezone-aware `datetime` only; naive datetimes are a bug except at the EXIF-parse boundary
  where they are immediately localised.
- Logging via the `logging` module with a Rich console handler + rotating file handler; no
  `print` outside the CLI layer.
- Errors: raise specific exception types; the pipeline catches per-photo errors, logs them,
  journals them, and continues — one bad photo must never stop the watcher.
- Keep modules small and single-purpose per the architecture in PLAN.md. No speculative
  abstraction, no premature plugin systems.
- Docstrings on public functions: what and why, not restating types.
