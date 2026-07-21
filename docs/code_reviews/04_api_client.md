# Code review — Step 04: Tags API client and tag cache (`448b701`)

Reviewed against `prompts/04_api_client.md`, `TagsAPI.lua`, `ApiConfig.lua`,
`MetadataMapper.lua`, and `../teamgym-sorter/docs/api/{tags-api.yaml,main-api.yaml}`.

## Must-fix

None found.

## Checked, no issues

- **HTTP constants**: `HTTP_TIMEOUT_SECONDS=30.0`, `RETRY_COUNT=1`, `RETRY_DELAY_SECONDS=2.0`
  match `ApiConfig.HTTP.{TIMEOUT,RETRY_COUNT,RETRY_DELAY_SECONDS}` exactly. Retry-once-then-raise
  semantics match `TagsAPI.lua`'s `makeAuthenticatedRequest` attempt loop (`1 + RETRY_COUNT`
  attempts, sleep only between attempts, error only after all attempts exhausted).
- **Status-code-agnostic parsing**: `_get`/`_parse_response_body` never call
  `raise_for_status()` and parse the JSON body regardless of HTTP status, matching
  `LrHttp.get`'s behaviour (no status inspection) and the fix already established in
  `auth.py` (step 03 review). `tags-api.yaml` confirms `/tags` returns the
  `{success:false, error, message}` envelope on 400/401/500, and a bare `{data, meta}` object
  (no `success` key) on 200 — `_parse_response_body`'s `body.get("success") is False` check
  correctly treats a missing key as success, not error.
- **Pagination**: `fetch_all_tags` follows `page`/`limit`/`meta.hasNext` per the prompt's
  explicit instruction (a deliberate simplification vs. `TagsAPI.lua`'s
  `totalPages = ceil(total/limit)` loop — both terminate on the same server-provided data,
  and the prompt calls for `hasNext`). `DEFAULT_PAGE_LIMIT=100` matches the API's documented
  max (`tags-api.yaml`: `limit` max 100) and `TagsAPI.lua`'s own default.
- **Client-side `competitionId` filtering**: confirmed the `/tags` endpoint has no such
  filter param in `tags-api.yaml`, so filtering post-parse is required, matching the code
  and its docstring.
- **Field shape parity**: `Team`/`Group`/`Award`/`Competition`/`Tag` fields match
  `tags-api.yaml`'s schemas. Cross-checked against `MetadataMapper.lua`'s actual field reads
  (`tag.team.{id,clubName,teamName,nationality,clubId}`, `tag.group.{name,roundType}`,
  `tag.award.name`, `tag.competition.{name,type}`) — no field the mapper reads is missing,
  and the `Group`/`Award` dataclasses correctly omit `id` (unused by the plugin) per the
  prompt's own field list.
- **Defensive parsing**: `id`/`startTime`/`competition.id` required (raise `TagParseError`);
  `team`/`group`/`award` degrade to `None` on missing/non-dict/null, and their own
  sub-fields degrade individually — matches the plugin's field-by-field reads and the
  prompt's requirement. A malformed tag is logged and skipped in `fetch_all_tags`, never
  aborting the whole fetch.
- **`Unauthorized`**: raised only on `error == "Unauthorized"`, matching
  `tags-api.yaml`'s 401 example and the plugin's `authentication_expired` branch.
- **`TagCache`**: atomic swap under lock with the fetch happening outside the lock (verified
  by a concurrent-reader test with an `Event`-gated fake client); `stale()` boundary
  (`>=`) and never-refreshed case match the prompt; `tags_sorted` returns a defensive copy.
  Uses a structural `Protocol` instead of depending on `ApiClient` directly, per PLAN.md's
  "dumb container" note.
- **File immutability / photo safety**: not applicable — no photo I/O in this step.
- **Cross-platform**: no path or platform-specific code in this step.
- **Tests**: no real network (`pytest_httpx`), no real sleeps (`FakeClock`), no real
  monotonic clock (injected `clock=lambda: ...` / `100.0`); covers pagination assembly,
  retry-then-success, retry exhaustion, unauthorized/generic error, non-JSON/non-object
  body, competitionId filtering, unparseable/non-object tag entries, missing `data` key,
  and cache staleness/atomic-swap/torn-read under a real background thread.
- ruff, mypy, pytest all clean (101 passed, 1 skipped — pre-existing Windows-skip from
  step 03).

## Minor, not fixed

`TagsAPI.lua`'s `makeAuthenticatedRequest` strips whitespace from the token
(`string.gsub(token, "%s+", "")`) before building the `Authorization` header; `ApiClient._get`
doesn't. Same call made in the step 03 review for `auth.py`'s token storage — no evidence
tokens ever contain embedded whitespace, and it isn't called out in `prompts/04_api_client.md`,
so left as-is rather than adding speculative sanitisation in a second place.
