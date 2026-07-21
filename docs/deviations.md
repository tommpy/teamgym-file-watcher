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
