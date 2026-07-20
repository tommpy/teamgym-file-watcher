# CLAUDE.md — teamgym-file-watcher

Python CLI that watches an import dir, matches photos to TeamGym competition tags from the
teamgym-sorter API by capture time, and moves them into a phase-based folder hierarchy.
Files are never modified — metadata lives in the API (re-applied in Lightroom later by the
existing plugin). **Read `PLAN.md` before doing anything.**

## Commands

```bash
uv sync                       # install deps
uv run pytest -q              # tests (must pass before every commit)
uv run ruff check . --fix && uv run ruff format .
uv run mypy src/
```

## Hard rules

- Follow `prompts/rules/python.md`, `prompts/rules/testing.md`, `prompts/rules/git.md`.
- **Parity with Lua is non-negotiable.** `mapping.py`, `hierarchy.py`, and `matcher.py` must
  reproduce the behaviour of `../lightroom-plugins/teamgym.lrdevplugin/{MetadataMapper,
  PhotoCollectionHierarchy, BulkPhotoTagger}.lua` exactly — read the Lua before porting, port
  the Lua tests too, and never "improve" the logic. If the Lua looks wrong, keep the behaviour
  and note it in code comments + `docs/deviations.md`.
- **Never modify photo files.** The watcher only reads EXIF and moves files. The JPEGs in
  `samples/` are real-camera/Lightroom EXIF fixtures for timestamp-extraction tests.
- No network in tests. Mock httpx; use `tmp_path` for all filesystem work.
- Cross-platform always: `pathlib` everywhere, no shell-outs except an optional documented
  exiftool fallback, no POSIX-only assumptions (case-insensitive filesystems exist).
- Never delete or overwrite user photos. Moves are collision-safe; failures leave the source
  file in place.
- Commit at the end of every prompt step (see git rules). Do not push.

## Sibling repos (read-only references)

- `../teamgym-sorter` — API docs in `docs/api/`, tag semantics in `docs/tagging.md`.
- `../lightroom-plugins/teamgym.lrdevplugin` — the Lua plugin this app must match.
