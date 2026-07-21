# Step 14 — Per-batch windowed queries replace the tag cache

Read `PLAN.md` (Key decisions -> "Batching / API economy"), `pipeline.py`, `tag_cache.py`,
`matcher.py`, and `cli.py`'s `_build_deps`. This is the step that actually changes the
batching strategy; steps 12 and 13 only prepared the ground (CLI/config, and the new
`ApiClient.fetch_tags_window` method).

## Goal

Replace the periodically-refreshed, whole-competition `TagCache` with one
`fetch_tags_window` call per processed batch, scoped to that batch's own photos'
capture-time range. Delete `tag_cache.py` and `matcher.match_one` — the tag list a batch gets
back is always small (a handful of tags at most), so a linear scan via the already-existing
`matcher.match_batch` is enough; there is no more persistent snapshot to binary-search.

Also add the "untagged" fallback: if no competition name has ever been resolved (no tag has
ever matched, in this run), route those photos to `<dest_root>/untagged/` directly instead of
the normal `Competitions/<name>/Random` hierarchy path. Per the user's stated usage
constraint, there is always an open tag active before the first photo is taken in practice —
`untagged` is the escape hatch for the case where that constraint doesn't hold, not the normal
"unmatched photo" path (that's still `Random`, same as today, once a competition name is
known).

## Requirements

1. In `pipeline.py`, add a small mutable helper (plain class, not a dataclass — it needs
   real mutation, not `frozen=True`):
   ```python
   class CompetitionNameCache:
       """Remembers the competition's display name once any batch resolves it via a matched
       tag, so a later batch whose photos don't themselves match a tag can still build the
       normal Competitions/<name>/Random path instead of falling back to `untagged`."""

       def __init__(self) -> None:
           self._name: str | None = None

       def observe(self, name: str | None) -> None:
           if name is not None:
               self._name = name

       @property
       def name(self) -> str | None:
           return self._name
   ```
2. `PipelineDeps`: remove the `tag_cache: TagCache` field and the `refresh_lock` field
   (nothing left to single-flight-guard — each batch does its own one-shot fetch). Remove the
   `match_one` callable default entirely. Add `competition_name: CompetitionNameCache =
   field(default_factory=CompetitionNameCache)`. Replace the `tag_cache._TagFetcher` import
   with a new `_TagWindowFetcher` `Protocol` defined in `pipeline.py`:
   ```python
   class _TagWindowFetcher(Protocol):
       def fetch_tags_window(self, competition_id: int, start: datetime, end: datetime) -> list[Tag]: ...
   ```
   `api_client` keeps its name but is now typed against `_TagWindowFetcher`.
3. Rewrite the per-batch flow (replacing `_maybe_refresh_tag_cache` and the current
   per-group inline `match_one` call inside `_process_one_group`). `process_batch` should now:
   a. Extract capture time for every group in the incoming batch up front (same isolated
      try/except around `deps.extract_capture_time` as today, keeping the `reason` string for
      groups that fail), producing something like a `list[tuple[PhotoGroup, datetime | None, str]]`.
   b. Collect the groups that got a real capture time. If that subset is non-empty: compute
      `window_start = min(...)`, `window_end = max(...)` over exactly those capture times, and
      call `deps.api_client.fetch_tags_window(deps.config.competition_id, window_start, window_end)`
      **once for the whole batch** — not once per group. Wrap this single call in
      `try: ... except Unauthorized as exc: raise AuthenticationRequired(...) from exc`, same
      message style as the old `_maybe_refresh_tag_cache`. If it raises before any group has
      been processed, return `BatchResult(outcomes=(), pending=tuple(group_list), auth_paused=True)`
      immediately — this must halt the *entire* batch (every group, including ones with no
      capture time), matching today's "an auth failure pauses everything, nothing is journaled
      as an error" contract.
   c. If the capture-time subset is empty (every group's EXIF extraction failed), skip the
      fetch entirely — there is nothing to look up.
   d. Run `matcher.match_batch` over `[(group, capture_time) for groups with a capture time]`
      against the fetched tag list (already sorted and competition_id-filtered by
      `fetch_tags_window`) to get each such group's matched tag or `None`.
   e. For every group, in original order (including ones with no capture time, whose tag is
      always `None`): map metadata as today, then resolve the destination competition name:
      - if a tag matched and `tag.competition.name is not None`: use it, and call
        `deps.competition_name.observe(tag.competition.name)`.
      - elif `deps.competition_name.name is not None`: use that (a previous batch already
        resolved it).
      - else: no name has ever been resolved — set `dest_dir = deps.config.dest_root / "untagged"`
        directly, skipping `deps.derive_destination_dir`/`hierarchy.py` entirely. Do **not**
        modify `hierarchy.py` for this — it must stay an exact Lua port per `CLAUDE.md`; the
        `untagged` fallback is a pipeline-level concern only.
      - otherwise, `dest_dir = deps.derive_destination_dir(deps.config.dest_root, metadata, name)`
        exactly as today.
      Move/journal/log/stats for each group exactly as today (`JournalStatus.RANDOM` still
      covers both the normal Random-folder case and the new `untagged` case — no new
      `JournalStatus` member; only the destination path differs).
   f. Keep the existing per-group `except Exception` isolation (one bad photo must not stop
      the batch) around steps (d)/(e)'s per-group work, same as today's `process_batch` loop.
4. `matcher.py`: delete `match_one` and its `bisect_right` import. Update the module
   docstring's "Two entry points" section to describe only `match_batch`. Delete `match_one`'s
   tests from `tests/test_matcher.py`. Do not change `match_batch` or `_in_window`.
5. Delete `src/teamgym_watcher/tag_cache.py` and `tests/test_tag_cache.py` entirely.
6. `api.py`: delete `fetch_all_tags` and its dedicated tests in `tests/test_api.py` — after
   this step nothing calls it (its only caller, `TagCache.refresh`, no longer exists). Keep
   `fetch_tags_window`, `parse_tag`, and the shared parsing/pagination helpers untouched.
7. `cli.py`'s `_build_deps`: remove the `TagCache(...)` construction and the initial
   `tag_cache.refresh(api_client)` call (and its `ApiError` handling) — there is nothing to
   prefetch any more; the first batch's own `fetch_tags_window` call is the first API request.
   Build `PipelineDeps` with `api_client=api_client` and a fresh `competition_name=CompetitionNameCache()`,
   no `tag_cache=`.
8. Remove `tag_refresh_seconds` now that nothing refreshes on an interval: the `Config` field,
   `build_config`'s parameter, the `--tag-refresh-seconds` CLI option in `cli.py` (all three
   commands), and `_validate_positive(tag_refresh_seconds, ...)`.
9. Tests:
   - `tests/test_pipeline.py`: replace the `TagCache`-backed fixture (`FakeApiClient` /
     `_deps` helper around the top of the file) with a fake exposing `fetch_tags_window`
     instead of `fetch_all_tags`, and drop the `TagCache` import/construction. Rename or
     replace the `TestTagCacheRefresh` class with tests for the new per-batch fetch. Cover:
     every photo matches a tag; a mix of matched/unmatched within one batch; a batch where
     some groups fail EXIF extraction (fetch still fires for the rest, using only their
     capture times for the window; if *none* have a capture time, assert the fake's
     `fetch_tags_window` was never called); the window passed to `fetch_tags_window` is
     exactly `[min, max]` over the groups that had a capture time; the `untagged` fallback
     (no tag ever matched, no competition name known) landing under `dest_root / "untagged"`;
     the name-persists-across-batches case (batch N resolves the name via a match, batch N+1
     is all-Random but still lands under `Competitions/<name>/Random`, not `untagged`);
     `AuthenticationRequired` from the fetch pausing the whole batch with every group
     (including no-capture-time ones) in `pending` and nothing journaled.
   - `tests/test_matcher.py`: remove `match_one` tests, keep `match_batch`/`_in_window` tests.
   - `tests/test_exif.py`: `TestTimeOffsetSeconds.test_time_offset_seconds_can_shift_a_boundary_match`
     currently imports `match_one`/`TagCache` directly — rewrite it to build a plain
     `list[Tag]` and call `matcher.match_batch` (or the underlying window-test logic) instead,
     keeping the same boundary-shift assertion (a photo one second after a tag's `end_time`
     doesn't match until `time_offset_seconds=-1` pulls it back inside the window).
   - `tests/test_cli.py`/`tests/test_config.py`: remove cases exercising the now-deleted
     `--tag-refresh-seconds` option / `tag_refresh_seconds` field.
   - `tests/integration/helpers.py`: `paginated_tags_callback`'s docstring references
     `ApiClient.fetch_all_tags` — update the wording to `fetch_tags_window` (the callback
     itself needs no logic change; it already ignores query params other than `page`, which
     is fine since it's only responsible for exercising pagination assembly, not simulating
     real server-side time-window filtering).
   - `tests/integration/test_process_once.py`: run the full suite and fix any assertion whose
     `/tags` request count or ordering no longer holds now that the fetch happens lazily, the
     first time a batch contains at least one photo with a capture time, rather than eagerly
     at startup. Do not weaken an assertion further than the behaviour change actually
     requires — most scenarios should still see exactly the same number of paginated requests,
     since `process-once` drains its (small, single-batch) fixture sets in one batch either
     way.
   - Grep the whole `tests/` tree for `TagCache`, `tag_cache`, `fetch_all_tags`, `match_one`,
     and `tag_refresh_seconds` to make sure nothing was missed.

## Definition of done

`uv run ruff check . --fix && uv run ruff format .`, `uv run mypy src/`, `uv run pytest -q`
all clean; commit `refactor: per-batch windowed tag queries replace the periodic full-competition cache`.
