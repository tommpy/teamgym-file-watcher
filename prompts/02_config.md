# Step 02 — Configuration

Read `PLAN.md` and `prompts/rules/`. Reference: `../lightroom-plugins/teamgym.lrdevplugin/ApiConfig.lua`.

## Goal
Typed TOML configuration with CLI overrides and validation.

## Requirements
1. `config.py` with a frozen `Config` dataclass:
   - `api_base_url: str` (e.g. `http://localhost:3001/api`; the production URL pattern is in
     ApiConfig.lua comments)
   - `competition_id: int`
   - `import_dir: Path`, `dest_root: Path`
   - `timezone: str | None` — IANA name used as fallback when the photo has no offset
     (None = local machine zone)
   - `time_offset_seconds: int = 0` — camera clock-skew correction
   - `tag_refresh_seconds: int = 60`, `stability_seconds: int = 3`, `batch_size: int = 25`
   - `dry_run: bool = False`
2. Load order: TOML file (`--config` path; default `teamgym-watcher.toml` in CWD) → CLI
   option overrides. Use stdlib `tomllib`.
3. Validation with actionable errors: import_dir exists and is a directory; dest_root
   creatable; import_dir not inside dest_root and vice versa; timezone resolvable via
   `zoneinfo`; positive intervals.
4. Ship `teamgym-watcher.example.toml` with comments for every field.
5. Wire `--config` and the override options into the CLI commands (still stubs otherwise).
6. Tests: parse valid/invalid TOML, override precedence, every validation error path,
   defaults.

## Definition of done
Checks pass; commit `feat: typed TOML configuration with CLI overrides`.
