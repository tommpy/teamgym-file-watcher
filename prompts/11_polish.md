# Step 11 — Polish, docs, and final review

## Goal
Release-ready repo: docs match reality, coverage enforced, a human can set up in minutes.

## Requirements
1. Rewrite `README.md` for the finished app (replace the build-pipeline framing; move build
   notes to `docs/building.md`): quick start (`uv run teamgym-watcher …` and `pipx install`),
   config reference table generated from `Config`, auth walkthrough, hierarchy examples for
   all three competition types, Zenfolio upload note, the Lightroom workflow note (import
   moved files → run the plugin's BulkPhotoTagger to tag from the API → collection builder
   yields collections matching the folders), troubleshooting (token expiry, unsupported RAW
   formats / exiftool fallback, timezone diagnostics), Windows + macOS specifics (long
   paths, service/launchd hints).
2. `docs/verification.md`: manual checklist confirming folder ↔ Lightroom parity — import a
   set of moved photos into Lightroom with the plugin installed, run BulkPhotoTagger, run
   the plugin's collection builder, and confirm collection paths are identical to the
   on-disk folder paths the watcher chose for the same photos.
3. Enforce coverage floors in `pyproject.toml` per `prompts/rules/testing.md`; add a
   coverage step to CI.
4. Full sweep: `uv run ruff check .`, `ruff format --check .`, `mypy src/`, `pytest -q`
   all clean; kill dead code, TODOs either resolved or filed in `docs/backlog.md`
   (candidates: sorter-API refresh tokens for >1 h unattended runs; direct Zenfolio upload).
5. Verify `teamgym-watcher.example.toml` matches the final `Config` exactly.

## Definition of done
All green; commit `docs: final documentation and release polish`.
