# Code review — step 01 (bootstrap)

Reviewed commit `ab6840d` (chore: bootstrap project skeleton, tooling, and CI).

No `mapping.py`/`hierarchy.py`/`matcher.py` exist yet, so Lua parity does not apply to this
step.

## Issues found

1. **Resource leak / Windows file lock — must-fix.**
   `setup_logging` (`src/teamgym_watcher/log.py`) removed its previously installed
   `RotatingFileHandler` via `root.removeHandler(handler)` without calling `handler.close()`.
   `removeHandler` does not close the underlying file object, so the old log file's handle
   stayed open indefinitely. Confirmed on Windows: after a second `setup_logging()` call
   pointed at a new log file, the first log file could not even be deleted
   (`WinError 32: The process cannot access the file because it is being used by another
   process`). In production this leaks a file descriptor on every reconfiguration; in tests
   it risks intermittent Windows failures when pytest garbage-collects old `tmp_path`
   directories that still hold a locked log file.

   **Fix:** close each handler before removing it in `setup_logging`. Added
   `test_setup_logging_closes_previous_file_handler_on_replacement` to `tests/test_log.py`,
   which asserts the old handler's stream is closed and that the old log file can be deleted
   immediately after replacement (this test failed before the fix on Windows).

## Checks re-run after the fix

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy src/` — clean (strict)
- `uv run pytest -q` — 12 passed

## Other areas checked, no issues

- File immutability: no code opens photo files at all yet (bootstrap step only).
- Safety: n/a, no move/journal logic yet.
- Cross-platform: `pathlib.Path` used throughout `cli.py`/`log.py`; CI matrix covers
  ubuntu/windows/macos.
- Tests: assertions are meaningful (exit codes, log content, handler names/counts), no
  network calls, no sleeps/real-clock dependence.
- Standards: ruff rule set and mypy strict config match `prompts/rules/python.md`; no dead
  code; `pyproject.toml` matches the step's stated dependencies and entry point.
