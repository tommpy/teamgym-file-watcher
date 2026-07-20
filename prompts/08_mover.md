# Step 08 — File mover and journal

Read `PLAN.md` (File handling) and `prompts/rules/python.md`. Safety rules in CLAUDE.md are
absolute: never overwrite or lose a photo.

## Goal
`mover.py` + `journal.py`: collision-safe physical moves of photos (and their companions)
into the derived hierarchy, with an idempotency journal.

## Requirements
1. Companion grouping: files sharing a basename (case-insensitive stem match) form a group —
   e.g. `IMG_0001.CR3` + `IMG_0001.JPG` + `IMG_0001.xmp`. RAW extensions (configurable set:
   cr2 cr3 nef nrw arw raf orf rw2 dng), JPEG (jpg jpeg), sidecar xmp. A group moves
   together to the same destination folder; the group's capture time comes from RAW first,
   else JPEG.
2. `move_group(group, dest_dir, *, dry_run) -> MoveResult`:
   - `dest_dir.mkdir(parents=True, exist_ok=True)`;
   - collision: if any target name exists, suffix the whole group `-1`, `-2`, … (stems stay
     aligned so pairs remain pairs);
   - atomic per file: `Path.replace` when same volume; else copy → flush+fsync → verify size
     → `replace` into place → delete source. Any failure leaves the source intact and
     reports a per-file error;
   - dry-run logs intended moves, touches nothing.
3. `journal.py`: append-only JSONL at `dest_root/.teamgym-watcher/journal.jsonl` — one record
   per group: timestamp, source paths, dest paths, tag id (or null), metadata applied,
   status (moved | random | error), message. `load_processed() -> set[str]` of source names
   for restart skip. Journal write failures must not block moves (log loudly instead).
4. Stats counter object (processed / matched / random / errors) shared with the watcher for
   the periodic summary line.
5. Tests: pair + solo grouping, case-insensitive stems, collision suffixing keeps pairs
   aligned, cross-device fallback (monkeypatch `Path.replace` to raise OSError once),
   failure-leaves-source-intact, dry-run touches nothing, journal round-trip and restart
   skip, unicode filenames.

## Definition of done
Checks pass; commit `feat: collision-safe mover with companion grouping and journal`.
