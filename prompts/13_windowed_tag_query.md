# Step 13 — Windowed tag query on the API client

Read `PLAN.md` and `api.py`. This step only adds a new method to `ApiClient`; it does not
remove `fetch_all_tags` or touch `tag_cache.py`/`pipeline.py`/`matcher.py` yet (step 14 rewires
the callers and then retires what becomes unused).

## Goal

Add `ApiClient.fetch_tags_window(competition_id, start, end)`, which fetches only the tags
whose window overlaps a given capture-time range, instead of the whole competition. This is
what lets a later step query per-batch (a handful of photos' capture-time span) instead of
paginating every tag for the competition on every refresh.

## Background — read this carefully, it is not obvious from the OpenAPI spec's wording

The OpenAPI description of `startTimeFrom`/`endTimeTo` reads like two independent one-sided
bounds ("starting after this time" / "ending before this time"). The sorter API's actual
server-side filter (`server/src/db/tags.ts`, `matchesTagsForAPIFilters`) is smarter than that
description **when both are supplied together**:

```
if (filters.startTimeFrom && filters.endTimeTo) {
  // overlap test, not two independent bounds:
  if (!(tagStart <= rangeEnd)) return false;
  if (!(tagEnd === null || tagEnd >= rangeStart)) return false;
}
```

So passing both params together returns every tag whose window **overlaps** `[startTimeFrom,
endTimeTo]` — including a tag that started long before `startTimeFrom`, and any currently-open
tag (`tagEnd === null` always satisfies the second condition). Passing only one of the two
falls back to the literal one-sided OpenAPI behaviour, which is NOT what we want. This means:

- Always pass both `startTimeFrom` and `endTimeTo` together — never just one.
- The exact `[min_capture_time, max_capture_time]` span of a batch's photos is enough; no
  lookback/lookahead padding is needed for correctness, because the filter is already an
  overlap test in both directions.
- Always pass `includeOpen=true` so an in-progress (open-ended) tag is never excluded.

## Requirements

1. Add to `ApiClient` in `api.py`:
   ```python
   def fetch_tags_window(
       self, competition_id: int, start: datetime, end: datetime, *, limit: int = DEFAULT_PAGE_LIMIT
   ) -> list[Tag]:
   ```
   - Format `start`/`end` as UTC ISO 8601 for the query params (`.astimezone(UTC).isoformat()`,
     mirroring `_parse_datetime`'s round-trip) and send them as `startTimeFrom`/`endTimeTo`
     together with `includeOpen: "true"` (or Python `True` — check how `_get`'s
     `Mapping[str, str | int]` param type is used elsewhere in the file and match it; widen
     the type alias if needed to accept a bool cleanly) and `page`/`limit`, following
     `meta.hasNext` exactly like `fetch_all_tags` does.
   - Filter returned tags client-side by `competition_id` — the endpoint is still global
     across all competitions, exactly as `fetch_all_tags` already documents and handles.
   - Same defensive per-tag parsing as `fetch_all_tags`: a tag that fails `parse_tag` is
     logged and skipped, never aborts the whole fetch.
   - Sort the result by `start_time` ascending before returning (matches `fetch_all_tags` and
     is what `matcher.match_batch` expects of its `tags_sorted` argument).
   - Give it a docstring explaining the overlap-filter semantics above in your own words, so a
     future reader doesn't have to rediscover it from the sorter API's TypeScript source.
   - Do not modify `fetch_all_tags`, `parse_tag`, or any other existing method/class in this
     file.
2. Tests in `tests/test_api.py` (add a new test class/section near the existing
   `fetch_all_tags` tests, following their existing `pytest-httpx` mocking style):
   - A single-page window fetch asserts the request was built with `startTimeFrom`,
     `endTimeTo`, and `includeOpen=true` all present and correctly formatted.
   - Pagination across multiple pages assembles the full result (reuse or mirror the existing
     `fetch_all_tags` pagination test's approach).
   - `competition_id` client-side filtering still drops tags belonging to other competitions
     from the mocked response.
   - A malformed tag in the response is skipped with a warning logged, not raised.
   - `Unauthorized` still propagates from a `success: false` / `error: "Unauthorized"` response.
   - Result is sorted by `start_time` even when the mocked response returns tags out of order.

## Definition of done

`uv run ruff check . --fix && uv run ruff format .`, `uv run mypy src/`, `uv run pytest -q`
all clean; commit `feat: windowed tag query on the API client`.
