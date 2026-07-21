# Verification: folder ↔ Lightroom collection parity

`hierarchy.py` is a line-for-line port of the Lightroom plugin's
`PhotoCollectionHierarchy.lua`, so the on-disk folders this watcher chooses and the
collections the plugin builds for the same photos should always describe the same path. The
automated test suite (`tests/test_hierarchy.py`, ported from
`PhotoCollectionHierarchyTests.lua`) checks the port's logic in isolation; this checklist is
the manual, end-to-end confirmation that the two real systems agree after any change to
either side (a hierarchy change on this side, a plugin release on the other, or a new
competition-tag shape from the API).

Run this after: a change to `hierarchy.py` or `mapping.py`, a plugin release that touches
`PhotoCollectionHierarchy.lua`/`MetadataMapper.lua`, or when setting up the watcher against a
competition/tag configuration you haven't verified before.

## Prerequisites

- A competition in teamgym-sorter with a representative spread of tags: at least one each of
  `International`, `Club`, and `InternationalClub` competition types, a nested phase (e.g.
  `Competition`), a flat phase (e.g. `Travelling`), and an `Awards` phase.
- A card or folder of test photos whose capture times land inside those tags' time windows,
  plus a few photos deliberately outside every window (to exercise the `Random` fallback).
- `teamgym-watcher` configured and authenticated against that competition.
- The Lightroom plugin (`teamgym.lrdevplugin`) installed in Lightroom Classic, configured
  against the same teamgym-sorter API and competition.

## Steps

1. **Sort with the watcher.** Run `teamgym-watcher process-once --dry-run` first and read the
   log output, then run it for real:

   ```bash
   uv run teamgym-watcher process-once --config teamgym-watcher.toml
   ```

   Record the resulting folder tree under `dest_root/Competitions/<Competition>/...` — e.g.
   `dir /s /b dest_root` (Windows) or `find dest_root -type f` (macOS/Linux) — as your
   reference list of `(photo, on-disk path)` pairs.

2. **Import into Lightroom.** Import the moved photos from `dest_root` into a Lightroom
   catalog with the plugin installed, without doing anything else to them yet.

3. **Run `BulkPhotoTagger`.** Use the plugin's bulk tagger on the imported photos so they're
   tagged from the API exactly as they would be in the existing (pre-watcher) workflow.

4. **Run the plugin's collection builder.** Build collections from the newly-tagged photos.

5. **Compare.** For every photo in your reference list from step 1:
   - Find which collection(s) the plugin placed it in.
   - Convert the collection's full path (its nesting from the competition root down) into a
     `/`-joined string and compare it against the on-disk path segments recorded in step 1
     (excluding `dest_root` itself, which has no collection equivalent).
   - They must match segment-for-segment, including the exact display-name substitutions
     (e.g. `PodiumTraining` folder ↔ "Podium Training" collection name) and the `"ClubName -
     TeamName"` / `"OlympicCode - ClubName"` composite segments.

6. **Check the `Random` cases.** The photos you deliberately put outside every tag's time
   window should have landed in a `Random` folder on disk (step 1) and, likewise, gone
   untagged by `BulkPhotoTagger` (or been placed by the collection builder wherever it puts
   unmatched photos) in step 3/4 — confirm the two "couldn't match" outcomes agree, not just
   the successful matches.

7. **Record the result.** Note the date, competition, plugin version, and watcher commit
   checked in `docs/code_reviews/` or the relevant PR — a quick "verified against tags X, Y,
   Z on \<date\>" note is enough; this isn't meant to be a formal sign-off document.

## If they disagree

- First suspect whichever side changed most recently — check `git log` on `hierarchy.py`/
  `mapping.py` here, and the plugin's own changelog.
- Confirm both sides are pointed at the *same* API base URL and competition id — a mismatch
  here produces confusing, not-actually-related divergence.
- If the divergence traces back to the Lua looking inconsistent rather than this port being
  wrong, do not "fix" the Python port unilaterally — see CLAUDE.md's parity rule and record
  the finding in [`docs/deviations.md`](deviations.md) instead.
