# Step 07 — EXIF timestamps and photo→tag matching

Read, in full, the timestamp + matching logic in
`../lightroom-plugins/teamgym.lrdevplugin/BulkPhotoTagger.lua`
(`extractTimestampsFromSelection`, `isoToUnixTimestamp`, `isPhotoInTagTimeWindow`,
`performPhotoTagMatching`). The Python port must reproduce its matching semantics.

## Goal
`exif.py` + `matcher.py`: capture-time extraction where **the photo is the source of truth
for timezone**, and the sorted-merge matcher.

## Requirements
1. `exif.py` — `extract_capture_time(path, config) -> CaptureTime`:
   - `DateTimeOriginal` via `exifread` (works for JPEG and TIFF-based RAW: NEF, CR2, ARW,
     DNG…). Verify against the real Lightroom-written JPEGs in `samples/` (their EXIF
     capture times are listed in the neighbouring `.json` files' context; assert exact
     values in tests). If a configured RAW extension is unsupported by exifread, implement
     the documented fallback: `exiftool` subprocess behind the same interface, enabled by
     config flag, with a clear startup check and error message;
   - timezone: `OffsetTimeOriginal` (EXIF 2.31) when present — this is authoritative; else
     `config.timezone` (IANA); else local machine zone. Record which source was used
     (logged per photo at debug level).
   - apply `config.time_offset_seconds`, return aware UTC datetime + source info;
   - missing DateTimeOriginal → typed error (pipeline will route the photo to Random and
     journal a warning — note the contract).
2. `matcher.py` — port `performPhotoTagMatching` as a generator-friendly function:
   photos sorted by capture time, tags sorted by startTime (TagCache provides), single
   forward pass with a persistent tag index; window test identical to
   `isPhotoInTagTimeWindow`: inclusive both ends, open-ended tag (endTime None) matches
   photo >= start; photo before current tag start → no match, don't advance; photo after tag
   end → advance tag index. First match wins, exactly one tag max.
3. `match_one(photo_time, cache) -> Tag | None` for the streaming watcher path (binary
   search over the sorted closed tags + open-tag check) — must agree with the batch matcher;
   property-test them against each other.
4. Tests: port the boundary semantics implied by the Lua (photo exactly at start, exactly at
   end, between tags, before all, after all, inside open tag); DST transition and
   non-UTC offsets; offset-in-photo overriding config timezone; `time_offset_seconds`
   shifting a boundary match; batch vs streaming equivalence over randomised inputs.

## Definition of done
Checks pass, coverage ≥ 90 % on `matcher.py`; commit
`feat: EXIF timestamp extraction and Lua-parity tag matching`.
