# Step 12 — CLI-only defaults and --local/--staging/--api-base-url environments

Read `PLAN.md` and `prompts/rules/`. This step touches `config.py` and `cli.py` only —
`tag_cache.py`/`matcher.py`/`pipeline.py` are untouched here (a later step retires them).

## Goal

Remove the TOML config file entirely. Every setting becomes a plain CLI option with a
literal default baked in, except `competition_id`, `import_dir`, and `dest_root`, which have
no sensible default and stay required. The API base URL is chosen via `--local`/`--staging`
(fixed URLs) or overridden directly via `--api-base-url`, instead of always being a required
free-form string.

## Background

- Current `config.py` reads an optional TOML file, merges CLI overrides (`None` = "not
  provided") into an untyped `dict[str, object]`, then coerces/validates each field with
  `_as_str`/`_as_int`/`_as_bool`/`_as_path`. That whole coercion layer exists only because
  TOML values need typing/validation. Typer already parses CLI option types for us (e.g.
  `competition_id: int`), so once the TOML path is gone there is nothing left to coerce —
  only to validate.
- Confirmed API deployment convention (`../teamgym-sorter/infra/apigw.tf` +
  `server/src/app.ts`): every route is mounted under `/api` regardless of custom domain, so
  the base URL used by `ApiClient` must always end in `/api`, matching today's
  `http://localhost:3001/api`-style value.
- Fixed environment URLs (append `/api` to each):
  - local: `http://localhost:3001`
  - staging: `https://api-staging.tagging.teamgymphotos.com`
  - prod: `https://api.tagging.teamgymphotos.com`
- `tag_refresh_seconds` stays in `Config`/CLI for now with its current default (`60`) — it is
  still consumed by `cli.py`'s `TagCache` wiring until step 14 deletes `tag_cache.py`.
  Removing it here would break this step's own tests. Do not touch it beyond leaving it as a
  normal option with a literal default (no more None-sentinel/override-merge dance).
- Three settings that today are **TOML-only** (no CLI override existed) must become real CLI
  options now, or they become permanently stuck at their defaults once the file is gone:
  `recursive`, `exiftool_enabled`, `exiftool_path`.

## Requirements

1. In `config.py`:
   - Add `Final` constants: `LOCAL_API_BASE_URL = "http://localhost:3001/api"`,
     `STAGING_API_BASE_URL = "https://api-staging.tagging.teamgymphotos.com/api"`,
     `PROD_API_BASE_URL = "https://api.tagging.teamgymphotos.com/api"`.
   - Add `resolve_api_base_url(*, local: bool, staging: bool, api_base_url: str | None) -> str`:
     an explicit `api_base_url` always wins (return it unchanged); else if both `local` and
     `staging` are `True`, raise `ConfigError("--local and --staging cannot both be set.")`;
     else `local` -> `LOCAL_API_BASE_URL`; else `staging` -> `STAGING_API_BASE_URL`; else (no
     flags) -> `PROD_API_BASE_URL`.
   - Delete `load_config`, `_read_toml`, `_require`, `_as_str`, `_as_int`, `_as_bool`,
     `_as_path`, `DEFAULT_CONFIG_FILENAME`, and `tomllib` import — none of this is needed once
     there's no file to parse and CLI values already arrive correctly typed.
   - Add `build_config(...) -> Config` taking every `Config` field as an already-typed keyword
     argument (same defaults as the `Config` dataclass fields, `api_base_url`/
     `competition_id`/`import_dir`/`dest_root` required with no default). It runs the
     existing validators (`_validate_positive` for `tag_refresh_seconds`/`stability_seconds`/
     `batch_size`, `_validate_import_dir`, `_validate_dest_root`, `_validate_not_nested`,
     `_validate_timezone`) unchanged, then constructs and returns `Config(...)` directly. Keep
     `Config` and `ConfigError` as they are (minus the file-not-found message, which no longer
     applies).
2. In `cli.py`:
   - Remove `_ConfigOption`, the `DEFAULT_CONFIG_FILENAME` import, `_build_overrides`, and the
     `config: _ConfigOption = None` parameter (and all `--config` handling) from `run`,
     `process_once`, and `auth`.
   - `_ApiBaseUrlOption` becomes a pure override: help text should say it takes precedence
     over `--local`/`--staging`. Add `_LocalOption` (`--local`, bool, default `False`, help
     mentions the local dev URL) and `_StagingOption` (`--staging`, bool, default `False`,
     help mentions the staging URL) following the existing `Annotated[...] = typer.Option(...)`
     pattern used elsewhere in the file.
   - Add CLI options for the three former TOML-only settings: `--recursive/--no-recursive`
     (default `False`), `--exiftool-enabled/--no-exiftool-enabled` (default `False`),
     `--exiftool-path` (default `"exiftool"`).
   - Every other option (`competition_id`, `import_dir`, `dest_root`, `timezone`,
     `time_offset_seconds`, `tag_refresh_seconds`, `stability_seconds`, `batch_size`,
     `dry_run`, plus the three new ones above) gets a literal default matching `Config`'s
     field default, EXCEPT `competition_id`/`import_dir`/`dest_root`, which must be required
     (use `typer.Option(..., "--flag", ...)` with the `...` sentinel, no Python-level default,
     so Typer enforces "must be passed").
   - Rewrite `_resolve_config` (or fold its body into each command — keep it a shared helper)
     to call `config.resolve_api_base_url(local=local, staging=staging, api_base_url=api_base_url)`
     then `config.build_config(api_base_url=<resolved>, competition_id=..., ...)` directly with
     the command's already-typed parameters, still catching `ConfigError` -> exit code
     `CONFIG_ERROR_EXIT_CODE` exactly as today. Keep the existing "Loaded configuration for
     competition..." info log.
   - Update `run`, `process_once`, and `auth` signatures to accept `local`, `staging`, and the
     three new options, threading them through to `_resolve_config` the same way the existing
     options are threaded.
3. Delete `teamgym-watcher.example.toml`.
4. Tests:
   - Rewrite `tests/test_config.py`: no TOML/file fixtures at all. Cover
     `resolve_api_base_url`'s four branches (override wins even with local/staging also set,
     local-only, staging-only, neither -> prod, both local and staging -> `ConfigError`) and
     `build_config`'s defaults plus every existing validation error path (missing
     import_dir, dest_root not creatable, nested dirs, bad timezone, non-positive intervals).
   - Update `tests/test_cli.py`: drop any case that exercises `--config` or a TOML fixture;
     invoke commands with explicit flags (including at least one case each for `--local`,
     `--staging`, default-is-prod, and the `--local --staging` conflict producing exit code
     `CONFIG_ERROR_EXIT_CODE`).
   - Grep the rest of the test suite (`tests/`) for `--config`, `DEFAULT_CONFIG_FILENAME`,
     `load_config`, or a `teamgym-watcher.toml` fixture, and update every hit the same way —
     do not leave a test invoking a removed option or function.

## Definition of done

`uv run ruff check . --fix && uv run ruff format .`, `uv run mypy src/`, `uv run pytest -q`
all clean; commit `refactor: CLI-only defaults and --local/--staging/--api-base-url environment flags`.
