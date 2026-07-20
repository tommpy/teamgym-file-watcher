# Step 04 — Tags API client and tag cache

Read `PLAN.md`. References: `../teamgym-sorter/docs/api/README.md` and
`docs/api/main-api.yaml` (tag shape, pagination meta), plus
`../lightroom-plugins/teamgym.lrdevplugin/TagsAPI.lua` (query params, retry-on-cold-start
behaviour) and `ApiConfig.lua` (timeout 30 s, 1 retry, 2 s delay).

## Goal
`api.py` + `tag_cache.py`: fetch ALL tags for a competition efficiently; local time-window
lookup. This is the batching strategy — the API is hit a few times per hour, never per photo.

## Requirements
1. Typed models (frozen dataclasses): `Tag` (id, competitionId, startTime/endTime as aware
   UTC datetimes, section, ageGroup, phase, apparatus, team {id, clubName, teamName,
   nationality, clubId}, group {name, roundType}, award {name}, competition {name, type}) —
   match the JSON shape the Lua code reads in `MetadataMapper.lua`/`TagsAPI.lua`. Unknown
   fields ignored; parse defensively, collect warnings not crashes.
2. `ApiClient` (httpx): bearer auth header, 30 s timeout, retry once on timeout/connect
   error after 2 s (Lambda cold start), raise typed errors incl. `Unauthorized` (→ maps to
   re-auth upstream). `fetch_all_tags(competition_id) -> list[Tag]`: follow pagination
   (`page`/`limit`, `meta.hasNext`) until exhausted; filter `competitionId`; sort by
   startTime.
3. `TagCache`: holds sorted closed+open tags; `refresh(client)` swaps atomically (thread
   -safe — watcher thread reads while refresher writes); `stale(now) -> bool` using
   `tag_refresh_seconds`; `tags_sorted` property. Matching itself lives in step 07 —
   keep this a dumb container.
4. Tests: pagination assembly across 3 pages, retry-then-success, unauthorized raise,
   defensive parsing of partial tags (null team/group/award), sort order, cache staleness
   and atomic swap under a reader thread.

## Definition of done
Checks pass; commit `feat: tags API client with pagination, retry, and tag cache`.
