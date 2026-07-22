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

## `api.py` — `ApiClient` takes a token *provider*, not a fixed token (no Lua equivalent)

Before refresh-token support (`auth.py`), `ApiClient` was constructed once in `_build_deps`
with a single bearer token string, valid for that JWT's ~1-hour lifetime. That was fine for
a single `process-once` drain, but `run` loops forever: the client would have kept using
that same token string for the whole run, and once it expired every subsequent request
would 401 forever — even though `get_valid_token` could now silently refresh a new one — so
step 16 changed `ApiClient.__init__`'s `token` parameter to accept `str | Callable[[], str]`.
`_build_deps` now passes a closure that re-calls `get_valid_token` (cheap: a cache-file read
unless a refresh/device-flow round-trip is actually due), resolved fresh before every
request. An `auth.AuthError` raised while resolving the token (e.g. `AuthenticationRequired`
because non-interactive silent refresh isn't possible) is translated to the existing
`Unauthorized`, so `pipeline.process_batch`'s pause-and-retry handling needed no changes.
