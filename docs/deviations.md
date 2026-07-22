# Deviations

Places where the Lua plugin's behaviour looks inconsistent or surprising, kept unchanged in the
Python port per CLAUDE.md ("if the Lua looks wrong, keep the behaviour and note it here").

## `MetadataMapper.lua` — missing/empty `ClubName` isn't a returned warning

`mapTagToMetadata` appends to its returned `warnings` table for every other unmapped/unknown
value (section, age group, apparatus, phase, competition type, nationality). But when
`tag.team` exists and `team.clubName` is missing or empty, it only calls `logger:warn(...)`
(which writes to the plugin's log file) — it does **not** `table.insert(warnings, ...)`. So a
tag with a team but no club name produces no warning visible to anything reading the returned
`MapResult`, only the plugin's own log file.

`mapping.py`'s `map_tag_to_metadata` reproduces this exactly: `logger.debug(...)` on a
missing/empty club name, nothing added to `MapResult.warnings`. This is very likely an
oversight in the Lua (the warning is silently swallowed from the caller's point of view), but
changing it would mean the Python port surfaces warnings the Lua never did for equivalent
tags — a parity break — so it stays as-is.

## `pipeline.py` — `untagged` fallback folder (no Lua equivalent)

When no competition name has been resolved yet in the current run (no tag has matched any
photo so far), the pipeline cannot construct a `Competitions/<Competition>/...` path.
Instead it moves the file to `<dest_root>/untagged/`.

The Lua plugin has no equivalent concept: it always has a full snapshot of every tag for the
competition available at the start of a BulkPhotoTagger run, so the competition name can
always be obtained from the matched tag (or the photo is simply left untagged in-catalog).
The `untagged` folder is a direct consequence of the per-batch windowed query strategy: the
first batch(es) may pre-date the earliest open tag in a fresh run, yielding no match and no
name. The stated usage constraint — at least one tag is already open before the first
photo is taken — keeps this rare in practice; the folder is easy to spot and re-sort after
the run completes.
