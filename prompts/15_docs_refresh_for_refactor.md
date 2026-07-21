# Step 15 — Docs sync for the CLI/environment/tag-query refactor

Read `PLAN.md`, `README.md`, `docs/deviations.md`. Steps 12-14 changed real behaviour (no more
config file, `--local`/`--staging`/`--api-base-url`, per-batch windowed tag queries, the
`untagged` fallback folder); this step is documentation-only — no `src/` or `tests/` changes.

## Goal

Make every doc describe the app as it now actually behaves. A new user reading `README.md`
should not encounter `--config`, a TOML file, or `tag_refresh_seconds`.

## Requirements

1. `PLAN.md`:
   - Architecture list: remove the `tag_cache.py` entry; update `config.py`'s one-line
     description (no more "TOML config + CLI overrides" — it's CLI-only defaults plus
     environment resolution now).
   - "Key decisions" -> "Batching / API economy": rewrite to describe the actual current
     strategy — one `fetch_tags_window` call per processed batch, scoped to that batch's own
     photos' capture-time span (`[min, max]`, no padding needed — explain briefly *why*: the
     sorter API's combined `startTimeFrom`+`endTimeTo` filter is an overlap test, not two
     independent bounds, so the exact span already catches tags that started earlier or are
     still open), instead of a periodically-refreshed whole-competition snapshot. Mention that
     this removes `tag_refresh_seconds`/the background refresh entirely.
   - Add a short note (wherever it fits best, likely near the hierarchy/competition-name
     material) about the `untagged` fallback folder: used only when no competition name has
     ever been resolved in the current run (no tag has matched yet) — distinct from the normal
     `Random` folder, which is used once a name is known but a specific photo still doesn't
     match a tag.
   - Update any mention of `--config`/TOML in this file to reflect the CLI-only model, and add
     the three environment URLs (local/staging/prod) where the API base URL is discussed.
2. `README.md`:
   - Remove the "copy the example config" quick-start step and the TOML config-reference
     table entirely.
   - Replace it with: a plain CLI quick-start (auth, dry-run, run, process-once, all via
     explicit flags — no config file); a full CLI options reference table generated from the
     real `Config`/CLI option set as it now stands (including `--local`/`--staging`/
     `--api-base-url` and their default-is-prod behaviour, and the three previously
     TOML-only settings that are now real flags: `--recursive`, `--exiftool-enabled`,
     `--exiftool-path`).
   - Update every command example that currently passes `--config teamgym-watcher.toml` to
     instead pass the flags that example actually needs.
   - If there's a Windows/macOS "run unattended" example referencing `--config ...`
     (`LaunchAgent`/service snippet), update it to the flag-based invocation.
3. `docs/verification.md`: update its `process-once --config teamgym-watcher.toml` example
   command to the flag-based equivalent.
4. `docs/deviations.md`: add an entry documenting the `untagged` fallback folder as a
   deliberate addition beyond the Lua plugin's behaviour (the Lua has no equivalent concept,
   since it always has a full tag snapshot available at import time) — note why: a
   consequence of moving to per-batch windowed queries, guarded by the stated usage
   constraint that a tag is always open before the first photo is taken; documented here for
   traceability, following the same file's existing format (one `##` section, explain what
   happens and why it's intentional).
5. Confirm nothing in any doc still references `teamgym-watcher.example.toml`, `--config`, or
   `tag_refresh_seconds` (`grep -ri` across `*.md` for these terms and fix every hit).

## Definition of done

No stale references remain (verify with a final grep); `uv run pytest -q` still passes
(docs-only change, but the gate is universal per `prompts/rules/git.md`); commit
`docs: sync README/PLAN/deviations with the CLI-only, windowed-query refactor`.
