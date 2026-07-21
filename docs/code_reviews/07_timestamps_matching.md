# Code review — step 07 (EXIF timestamps and photo→tag matching)

Reviewed commit `7c88e5b` (`feat: EXIF timestamp extraction and Lua-parity tag matching`)
against `BulkPhotoTagger.lua`'s `extractTimestampsFromSelection`, `isoToUnixTimestamp`,
`isPhotoInTagTimeWindow`, and `performPhotoTagMatching`.

## Issues found and fixed

1. **`match_one` disagreed with `match_batch` at a touching tag boundary (must-fix).**
   `match_one`'s binary search picked the *later* tag when one tag's `end_time` exactly
   equals the next tag's `start_time` (e.g. Qualification ending the instant Finals
   begins — plausible for a real competition schedule, and not excluded by the sorter
   API's non-overlap guarantee). `match_batch`'s sequential scan always finds the
   *earlier* tag first and its window check is inclusive at the end, so the Lua's
   effective tie-break is "earlier tag wins." Fixed `match_one` to also check the tag
   immediately before its bisect candidate and prefer it when its window also contains
   the photo. Added a regression test
   (`TestBoundarySemantics::test_touching_tags_at_shared_boundary_prefer_the_earlier_tag`)
   and extended the randomised property test to occasionally generate touching
   (zero-gap) tags so fuzzing exercises this path too.

2. **`_read_via_exiftool` could raise an uncaught exception on malformed subprocess
   output (must-fix).** Invalid JSON, an empty/non-list result, or non-dict/non-string
   fields from `exiftool` would raise `json.JSONDecodeError`, `IndexError`, or
   `TypeError` instead of a typed `ExifError`, which the pipeline can't catch —
   violating the "one bad photo must never stop the watcher" rule. Now: JSON decode
   failures and unexpected shapes raise `ExifToolUnavailableError`; non-string
   `DateTimeOriginal`/`OffsetTimeOriginal` values are treated as absent (same
   downstream behaviour as a photo with no tag at all). Added three tests covering
   invalid JSON, an empty list, and a non-string field.

## Checklist

1. **Lua parity** — `match_batch`'s window test and forward-only tag index are a direct
   line-for-line port of `isPhotoInTagTimeWindow`/`performPhotoTagMatching`; inclusive
   bounds, open-ended-tag handling, and first-match-wins all verified against the Lua
   text. `exif.py` has no Lua counterpart (the plugin gets capture time from Lightroom's
   catalog, not raw EXIF) — its design follows PLAN.md's "photo is the source of truth
   for timezone" decision instead.
2. **File immutability** — `exif.py` only ever opens photos with `"rb"`; the `exiftool`
   subprocess is invoked read-only (no `-overwrite_original` or similar). No writes.
3. **Safety** — no delete/overwrite paths in this step. Per-photo failures raise typed
   `ExifError` subclasses; nothing here can crash the watcher loop except a
   misconfigured `exiftool_path`, which is caught at startup by
   `ensure_exiftool_available` before any photos are processed.
4. **Cross-platform** — `pathlib.Path` throughout; `subprocess.run` uses an argument
   list (never `shell=True`); no POSIX-only assumptions.
5. **Tests** — boundary semantics (start/end inclusive, gaps, before/after-all, open
   tag, touching tags), DST transition, non-UTC offset equivalence, photo-offset
   overriding config timezone, `time_offset_seconds` shifting a boundary match, and
   batch/streaming agreement (both fixed boundary cases and 200 randomised trials) are
   all covered. Real Lightroom JPEGs in `samples/` are used for the extraction tests;
   synthetic hand-built TIFF/EXIF blobs cover cases the samples can't (missing tag,
   photo-carried offset, `exiftool` fallback).
6. **Standards** — `ruff check`, `ruff format --check`, and `mypy --strict` all clean;
   100% coverage on both `matcher.py` and `exif.py` (well above the 90% bar).
