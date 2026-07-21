"""Tests for matcher.py: the port of BulkPhotoTagger.lua's photo->tag matching.

Boundary cases mirror the semantics implied by ``isPhotoInTagTimeWindow`` /
``performPhotoTagMatching`` in ``BulkPhotoTagger.lua`` (inclusive both ends, open-ended
tag = start-only check, first match wins). Every boundary case is checked through both
:func:`match_batch` and :func:`match_one` via :func:`_match_both` so the two algorithms'
agreement is exercised continuously, not just in the dedicated randomised test.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone

from teamgym_watcher.api import Competition, Tag
from teamgym_watcher.matcher import match_batch, match_one


def _tag(id_: int, start: datetime, end: datetime | None = None) -> Tag:
    return Tag(
        id=id_,
        competition_id=1,
        start_time=start,
        end_time=end,
        section=None,
        age_group=None,
        phase=None,
        apparatus=None,
        team=None,
        group=None,
        award=None,
        competition=Competition(name="X", type="INTERNATIONAL"),
    )


class _FakeCache:
    """Structural stand-in for TagCache: only `.tags_sorted` is needed by match_one."""

    def __init__(self, tags: list[Tag]) -> None:
        self._tags = tags

    @property
    def tags_sorted(self) -> list[Tag]:
        return list(self._tags)


def _match_both(capture_time: datetime, tags_sorted: list[Tag]) -> Tag | None:
    """Run both matchers on a single photo and assert they agree; return the result."""
    streaming_result = match_one(capture_time, _FakeCache(tags_sorted))
    (_, batch_result), *rest = list(match_batch([("photo", capture_time)], tags_sorted))
    assert rest == []
    assert batch_result == streaming_result
    return streaming_result


T0 = datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC)


class TestBoundarySemantics:
    def test_photo_exactly_at_tag_start_matches(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=30))

        assert _match_both(T0, [tag]) == tag

    def test_photo_exactly_at_tag_end_matches(self) -> None:
        end = T0 + timedelta(minutes=30)
        tag = _tag(1, T0, end)

        assert _match_both(end, [tag]) == tag

    def test_photo_between_tags_in_a_gap_does_not_match(self) -> None:
        tag_a = _tag(1, T0, T0 + timedelta(minutes=10))
        tag_b = _tag(2, T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
        gap_time = T0 + timedelta(minutes=15)

        assert _match_both(gap_time, [tag_a, tag_b]) is None

    def test_photo_before_all_tags_does_not_match(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))

        assert _match_both(T0 - timedelta(minutes=1), [tag]) is None

    def test_photo_after_all_closed_tags_does_not_match(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))

        assert _match_both(T0 + timedelta(minutes=11), [tag]) is None

    def test_photo_inside_open_ended_tag_matches(self) -> None:
        tag = _tag(1, T0, None)

        assert _match_both(T0 + timedelta(days=1), [tag]) == tag

    def test_photo_before_open_ended_tag_start_does_not_match(self) -> None:
        tag = _tag(1, T0, None)

        assert _match_both(T0 - timedelta(seconds=1), [tag]) is None

    def test_matches_second_tag_not_first_when_after_first_ends(self) -> None:
        tag_a = _tag(1, T0, T0 + timedelta(minutes=10))
        tag_b = _tag(2, T0 + timedelta(minutes=10, seconds=1), T0 + timedelta(minutes=20))
        photo_time = T0 + timedelta(minutes=15)

        assert _match_both(photo_time, [tag_a, tag_b]) == tag_b

    def test_no_tags_at_all_does_not_match(self) -> None:
        assert _match_both(T0, []) is None

    def test_touching_tags_at_shared_boundary_prefer_the_earlier_tag(self) -> None:
        """One phase's end_time can equal the next phase's start_time exactly (e.g.
        Qualification ending the instant Finals begins) -- the non-overlap guarantee
        doesn't rule this out. Both tags' windows contain that instant; the Lua's scan
        order means the earlier tag (encountered first) always wins the tie."""
        boundary = T0 + timedelta(minutes=10)
        tag_a = _tag(1, T0, boundary)
        tag_b = _tag(2, boundary, T0 + timedelta(minutes=20))

        assert _match_both(boundary, [tag_a, tag_b]) == tag_a


class TestNonUtcOffsetsCompareByInstant:
    def test_photo_in_different_offset_representing_same_instant_still_matches(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))
        # T0 is 2026-07-20T10:00:00Z; expressed with a +02:00 offset that's the same instant.
        same_instant_plus_two = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert same_instant_plus_two.astimezone(UTC) == T0

        assert _match_both(same_instant_plus_two, [tag]) == tag


class TestBatchSortsUnorderedInput:
    def test_match_batch_yields_results_in_capture_time_order_regardless_of_input_order(
        self,
    ) -> None:
        tag = _tag(1, T0, T0 + timedelta(hours=1))
        photos = [
            ("third", T0 + timedelta(minutes=20)),
            ("first", T0),
            ("second", T0 + timedelta(minutes=10)),
        ]

        results = list(match_batch(photos, [tag]))

        assert [item for item, _ in results] == ["first", "second", "third"]
        assert all(matched == tag for _, matched in results)


class TestRandomisedAgreement:
    """DoD: 'batch vs streaming equivalence over randomised inputs'.

    Generates random *non-overlapping* tag sets (the invariant match_one relies on, per
    its docstring, and which the sorter API guarantees per mapping.py) -- including some
    that touch exactly at a shared boundary instant, which the non-overlap guarantee
    permits and which match_one must tie-break the same way match_batch does -- and
    random photo times spanning before/inside/between/after them, then asserts
    match_batch and match_one agree on every one. No hypothesis dependency: plain seeded
    `random` keeps this deterministic without adding a new dependency.
    """

    def _random_tags(self, rng: random.Random, count: int) -> list[Tag]:
        tags: list[Tag] = []
        cursor = T0
        for i in range(count):
            cursor += timedelta(seconds=rng.randint(0, 5))
            start = cursor
            duration = timedelta(seconds=rng.randint(1, 10))
            end: datetime | None = start + duration
            tags.append(_tag(i, start, end))
            # Occasionally leave zero gap: the next tag touches this one's end exactly.
            gap = 0 if rng.random() < 0.2 else 1
            cursor = start + duration + timedelta(seconds=gap)
        if tags and rng.random() < 0.3:
            last = tags[-1]
            tags[-1] = _tag(last.id, last.start_time, None)
        return tags

    def _random_photo_time(self, rng: random.Random, tags: list[Tag]) -> datetime:
        if not tags:
            return T0 + timedelta(seconds=rng.randint(-100, 100))
        first_start = tags[0].start_time
        last = tags[-1]
        last_bound = (
            last.end_time if last.end_time is not None else last.start_time + timedelta(seconds=20)
        )
        span_seconds = int((last_bound - first_start).total_seconds())
        offset = rng.randint(-30, span_seconds + 30)
        return first_start + timedelta(seconds=offset)

    def test_batch_and_streaming_agree_over_many_random_trials(self) -> None:
        rng = random.Random(1234)
        for _ in range(200):
            tags = self._random_tags(rng, rng.randint(0, 6))
            cache = _FakeCache(tags)
            photos = [(i, self._random_photo_time(rng, tags)) for i in range(20)]

            batch_results = dict(match_batch(photos, tags))

            for item, capture_time in photos:
                assert batch_results[item] == match_one(capture_time, cache)
