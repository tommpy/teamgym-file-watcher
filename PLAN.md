# teamgym-file-watcher — Design

A cross-platform (Windows/macOS) Python CLI that watches an import directory for new photos,
matches each photo to its TeamGym competition tag (from the teamgym-sorter API) by capture
time, and physically moves it into an on-disk folder hierarchy that mirrors the Lightroom
plugin's phase-based collection hierarchy — ready for direct upload to Zenfolio.

## Why

Exporting 30,000 photos from Lightroom after a competition is far too slow. If photos land in
the right folder structure on disk *before* Lightroom, they can be uploaded to Zenfolio
immediately.

## Scope decision: no file metadata

Investigation of Lightroom-saved samples proved that Lightroom **never writes plugin custom
metadata to files** — `setPropertyForPlugin` data is catalog-only, and "Save Metadata to
File" emits only standard XMP. There is therefore no Lightroom-compatible file format to
write, and none is needed: **the API is the source of truth**. The watcher only sorts and
moves files. When photos are later imported into Lightroom, the existing plugin's
BulkPhotoTagger re-tags them from the API exactly as today, and its collection builder
produces collections that match the on-disk folders (both derive from the same hierarchy
logic).

## Source-of-truth references (sibling repos)

| Concern | Reference |
|---|---|
| API endpoints, auth, pagination | `../teamgym-sorter/docs/api/README.md`, `../teamgym-sorter/docs/api/main-api.yaml` |
| Tag model & semantics | `../teamgym-sorter/docs/tagging.md` |
| API → metadata mapping (MUST match exactly) | `../lightroom-plugins/teamgym.lrdevplugin/MetadataMapper.lua` (+ `MetadataMapperTests.lua`) |
| Folder hierarchy rules (MUST match exactly) | `../lightroom-plugins/teamgym.lrdevplugin/PhotoCollectionHierarchy.lua` (+ `PhotoCollectionHierarchyTests.lua`) |
| Metadata field ids/enums | `../lightroom-plugins/teamgym.lrdevplugin/MyMetadataDefinitionFile.lua` |
| Timestamp extraction & photo→tag matching | `../lightroom-plugins/teamgym.lrdevplugin/BulkPhotoTagger.lua` |
| Device-flow auth behaviour | `../lightroom-plugins/teamgym.lrdevplugin/ApiAuthentication.lua`, `ApiConfig.lua` |

## Architecture

```
teamgym_watcher/
  cli.py          # Typer CLI: run / process-once / auth / dry-run flags
  config.py       # CLI-only defaults + environment URL resolution (--local/--staging/--api-base-url)
  auth.py         # GitHub OAuth device flow (RFC 8628) against sorter API; token cache in platformdirs
  api.py          # httpx client: paginated tag fetch, retry w/ backoff (Lambda cold starts)
  mapping.py      # Port of MetadataMapper.lua (enum maps, nationality maps, olympic-code reverse map)
  hierarchy.py    # Port of PhotoCollectionHierarchy.lua path derivation (phase-based) + sanitisation
  exif.py         # Capture time + timezone from photo metadata (source of truth = the photo)
  matcher.py      # Sorted merge photo→tag matching (port of BulkPhotoTagger step 4)
  mover.py        # Grouping RAW+JPEG pairs, collision-safe atomic moves
  watcher.py      # watchdog observer + polling fallback; file-stability debounce; batch queue
  pipeline.py     # Orchestration: detect → stable → extract time → match → map → derive path → move
  journal.py      # JSONL journal of processed files (idempotency, resume, audit)
  log.py          # Rich console + rotating file logs
```

Note: `mapping.py` still exists even without metadata writing — the hierarchy rules operate
on the *mapped display values* (e.g. FINALS→"Competition", GBR→"Great Britain"), so the
mapper is the bridge between API tags and folder names, exactly as in the plugin.

## Key decisions

**Batching / API economy.** Do NOT query the API per photo. Photos are drained from the watch
queue in batches (default 25). For each batch, one `fetch_tags_window` call is made, scoped
to that batch's own photos' capture-time span (`[min_capture_time, max_capture_time]` — no
padding needed). Why no padding? The sorter API's `startTimeFrom`+`endTimeTo` filter is an
overlap test, not two independent bounds: a tag is returned if its window overlaps the query
range at all, so the exact photo span already catches tags that started earlier or whose end
is still open. This replaces the old whole-competition snapshot + `tag_refresh_seconds`
periodic refresh entirely — there is no background cache thread, and no `tag_refresh_seconds`
setting. 30,000 photos ≈ a handful of API calls per hour regardless of volume.

**Timestamps & timezones.** The photo is the source of truth. Read `DateTimeOriginal` plus
`OffsetTimeOriginal` (EXIF 2.31) when present; when absent, apply the configured fallback
(`timezone` in config, defaulting to the local machine zone). Convert to UTC and compare
against tag `startTime`/`endTime` (UTC ISO-8601), replicating `isPhotoInTagTimeWindow`
semantics exactly (inclusive bounds; open-ended tag = start-only check; first match wins).
EXIF reading via `exifread` (pure Python, covers JPEG + TIFF-based RAW); documented
`exiftool` fallback if a RAW format (e.g. CR3) proves unsupported. The sample JPEGs in
`samples/` are test fixtures for extraction.

**Hierarchy.** Phase-based hierarchy only (no "By Team" duplication on disk). `hierarchy.py`
ports `deriveInternationalPath`, `deriveClubPath`, `deriveInternationalClubPath`,
`classifyPhaseType`, and `sanitizeCollectionName` exactly, including: Random fallback at the
first missing level, Awards and Flat phase handling, "ClubName - TeamName" and
"OlympicCode - ClubName" composite segments, and display-name substitutions
("PodiumTraining" → "Podium Training", etc.). Path on disk:
`<dest_root>/Competitions/<Competition>/<...segments...>/<file>`.
Unmatched photos flow through the same rules → they land in `Random` folders, same as the plugin.

**`untagged` fallback folder.** If *no* competition name has been resolved yet in the current
run (no tag has matched any photo so far), the `Competitions/<Competition>/...` root cannot
be constructed. In that case the file is placed in `<dest_root>/untagged/` instead of
`Random`. Once at least one tag match has been seen and a competition name is known, all
subsequent unmatched photos go to the normal `Random` folder as usual. This situation arises
because per-batch windowed queries mean the first few batches may all pre-date the earliest
open tag — a consequence documented in `docs/deviations.md`.

**File handling.** Mixed inputs (JPEG, RAW, RAW+JPEG pairs). Files are never modified —
only moved. Companion files sharing a basename (RAW+JPEG, any pre-existing sidecar) are
grouped and moved together; group capture time comes from RAW first, else JPEG. A file is
only processed once its size has been stable for `stability_seconds` (default 3) — card
ingests are slow writers. Moves are collision-safe (never overwrite; `-1`, `-2` suffixes)
and cross-device safe (copy+fsync+rename+delete when rename fails). Every action is appended
to a JSONL journal; already-journaled files are skipped on restart.

**Auth.** Same GitHub device flow as the plugin: `POST /api/device/start`, poll
`POST /api/device/poll`, JWT cached (with expiry from decoded `exp`) in the platform config dir.
⚠️ Known constraint: JWTs last 1 hour and the API has no refresh — truly unattended runs will
pause and prominently log a re-auth URL when the token expires. (Future sorter-API improvement:
long-lived tokens or refresh flow; out of scope here.)

**Unattended operation.** `teamgym-watcher run` loops forever: watchdog events + a periodic
rescan sweep (belt and braces, and required on network shares). Rich, timestamped logging of
every detection, match, and move; a periodic one-line stats summary
(processed / matched / random / errors). `--dry-run` logs everything but moves nothing.
`process-once` drains the current directory and exits (useful for tests and catch-up).

## Toolchain & standards

- Python ≥ 3.12, `uv` for env/packaging, single package, `pyproject.toml` only.
- Deps: `typer`, `rich`, `httpx`, `watchdog`, `exifread`, `platformdirs` (tests: `pytest`, `pytest-httpx`, `pytest-cov`).
- Lint/format: `ruff` (lint + format). Types: `mypy --strict` on `src/`.
- Tests: pytest, no network (httpx mocked), tmp_path filesystems; Lua test cases from
  `MetadataMapperTests.lua` / `PhotoCollectionHierarchyTests.lua` ported as parametrised tests.
- CI: GitHub Actions matrix — ubuntu / windows / macos × Python 3.12 — ruff, mypy, pytest.
- Run with minimal setup: `uv run teamgym-watcher …` or `pipx install`.

## Build process (agentic)

Upfront thinking lives in this plan and the numbered prompts in `prompts/` (written by a
frontier model). Execution is cheap: `scripts/run_build.ps1` (or `.sh`) feeds each prompt to
`claude -p` with **Sonnet**, then runs the `prompts/tasks/code_review.md` review pass, then
gates on `uv run pytest` + a clean committed tree before advancing. Progress is recorded in
`.build_progress` so the runner is resumable. Each step ends with a commit; the review pass
commits fixes.

## Risks

1. **RAW EXIF coverage** — exifread may not parse every RAW flavour (notably CR3);
   mitigated by the documented exiftool fallback and an early check in step 07.
2. **Token expiry mid-run** — surfaced loudly in logs; queue pauses, nothing is lost.
3. **Clock skew camera↔server** — configurable `time_offset_seconds` correction, logged when set.
4. **Folder ↔ Lightroom collection drift** — both derive from the same ported logic; the
   Lua-parity tests plus `docs/verification.md` checklist guard against divergence.
