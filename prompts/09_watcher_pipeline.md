# Step 09 — Watcher and pipeline orchestration

Read `PLAN.md` (Key decisions) again before starting. This step wires everything together
into the long-running CLI.

## Goal
`watcher.py` + `pipeline.py` + full `cli.py`: unattended watch loop with batching, tag-cache
refresh, and rich logging.

## Requirements
1. `watcher.py`: watchdog `Observer` on `import_dir` (non-recursive by default; `recursive`
   config flag) feeding a thread-safe queue, PLUS a periodic full rescan (every 30 s) that
   enqueues anything unjournaled — dedup by path. Stability gate: a file is ready when its
   size is unchanged across `stability_seconds`; unstable files are re-queued. Ignore
   non-photo extensions and hidden/temp files (`.` `~` prefixes, `.tmp`).
2. `pipeline.py` — `process_batch(groups, deps) -> BatchResult`, for each group:
   a. extract capture time (photo-authoritative timezone, step 07);
   b. refresh tag cache if stale (single flight — one refresher at a time);
   c. `match_one` → tag or None;
   d. tag → `map_tag_to_metadata`; None or no capture time → empty metadata (hierarchy
      routes to Random exactly like the plugin);
   e. derive path → `move_group` (files are never modified, only moved);
   f. journal + stats. Per-group exceptions are caught, logged with filename + traceback at
      debug, journaled as error, and processing continues.
3. `AuthenticationRequired` handling: pause consumption, log the device-flow instruction
   loudly (WARNING with Rich panel) every 60 s, resume on success. In `--non-interactive`
   mode keep photos queued; never drop work.
4. `cli.py` final form:
   - `run` — auth check, initial full tag fetch, drain existing files, then watch forever;
     graceful Ctrl-C (finish current batch, flush journal, print stats);
   - `process-once` — drain current directory and exit with summary + non-zero on errors;
   - shared flags: `--config`, `--dry-run`, `--verbose`, `--non-interactive`, `--log-file`.
5. Logging: per-photo INFO line `filename → tag#id (Phase/Team/Apparatus) → relative/dest`,
   or `→ Random (reason)`; DEBUG detail; summary line every 60 s and on batch completion:
   `processed=N matched=N random=N errors=N queued=N`.
6. Tests: fake deps (in-memory tag cache, tmp dirs) — batch flow happy
   path; unmatched → Random; error isolation (one poison file doesn't stop the batch);
   stability re-queue; rescan picks up files created while observer was down; Ctrl-C
   handling via injected event; `process-once` exit codes. Keep watchdog itself out of unit
   tests (integration covers it); test the queue/stability logic directly.

## Definition of done
Checks pass; commit `feat: watch loop and processing pipeline`.
