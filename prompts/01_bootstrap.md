# Step 01 — Project bootstrap

Read `PLAN.md`, `CLAUDE.md`, and all files in `prompts/rules/` first.

## Goal
A committed, CI-green project skeleton with tooling, an installable CLI stub, and GitHub
Actions.

## Requirements
1. `git init` (if not already a repo) with `main` as default branch. Add `.gitignore`
   (Python, uv, .venv, __pycache__, .build_progress, .pytest_cache, .mypy_cache,
   .ruff_cache, coverage files).
2. `uv init` style `pyproject.toml`: package `teamgym-watcher`, `src/teamgym_watcher/`
   layout, `requires-python = ">=3.12"`, deps `typer`, `rich`, `httpx`, `watchdog`,
   `exifread`, `platformdirs`; dev deps `pytest`, `pytest-httpx`, `pytest-cov`, `ruff`,
   `mypy`. `[project.scripts] teamgym-watcher = "teamgym_watcher.cli:app"` (adjust to
   Typer's entry convention).
3. Ruff config per `prompts/rules/python.md`; mypy strict config scoped to `src/`.
4. `src/teamgym_watcher/__init__.py` with `__version__`, `cli.py` with a Typer app exposing
   `run`, `process-once`, and `auth` subcommands that currently log "not implemented" and
   exit non-zero, plus a `--version` option.
5. `log.py`: `setup_logging(verbose: bool, log_file: Path | None)` — Rich console handler +
   optional `RotatingFileHandler` (5 MB × 5). Used by the CLI stubs.
6. Tests: CLI invocation tests via `typer.testing.CliRunner` (help text, version, stub exit
   codes); a test that `setup_logging` attaches handlers idempotently.
7. GitHub Actions `.github/workflows/ci.yml`: on push + PR; matrix
   `os: [ubuntu-latest, windows-latest, macos-latest]`, Python 3.12; steps: install uv
   (astral-sh/setup-uv), `uv sync`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy src/`, `uv run pytest -q`.
8. Verify locally: ruff, mypy, pytest all clean.

## Definition of done
All checks pass; single commit `chore: bootstrap project skeleton, tooling, and CI`.
