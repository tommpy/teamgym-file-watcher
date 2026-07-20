# Review: Step 02 — Configuration

Reviewed commit `3ea16c5` (feat: typed TOML configuration with CLI overrides).

Not applicable this step: `mapping.py`/`hierarchy.py`/`matcher.py` Lua parity (no such
files touched); file-move safety (config.py never touches photos).

## Issue found and fixed

**`dest_root` could be created on disk before the nested-directory check rejected it.**
`_build_config` called `_validate_dest_root` (which does
`dest_root.mkdir(parents=True, exist_ok=True)`) *before* `_validate_not_nested`. A config
with `dest_root` set to a path inside `import_dir` — which is supposed to be rejected —
still left a stray directory created inside the user's import folder, since the mkdir ran
first and the rejection only happened afterwards. Reproduced directly against
`load_config`: the nested directory existed on disk after `ConfigError` was raised.

This matters because `import_dir` is the live folder the watcher scans for the user's
photos; a validation pass silently dropping a new subfolder into it before failing is an
unwanted filesystem mutation.

Fix: reordered `_build_config` so `_validate_not_nested` runs before
`_validate_dest_root`'s `mkdir` (`config.py`). `Path.resolve()` doesn't require the path to
exist, so the nesting check works unchanged on a not-yet-created `dest_root`.

Added `test_dest_root_inside_import_dir_is_not_created_before_rejection` in
`tests/test_config.py`, asserting the nested path is never created when rejected.

## Checks

`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/`,
`uv run pytest -q` — all pass (39 tests).

## Notes (not acted on)

- `cli.py`'s three commands (`run`, `process-once`, `auth`) repeat an identical 13-parameter
  signature and `_build_overrides` call. This is Typer's usual shape (each command needs an
  explicit parameter list for CLI generation) and isn't a rule violation, so left as-is per
  the "don't refactor beyond what's asked" guidance.
- `_validate_not_nested` compares resolved paths case-sensitively; on case-insensitive
  filesystems (Windows/macOS default) two paths differing only in case would not be caught
  as nested/equal. Not in the step's explicit requirements and not acted on, but worth
  keeping in mind if it ever surfaces in practice.
