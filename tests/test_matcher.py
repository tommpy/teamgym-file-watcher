"""Tests for matcher.py: the port of BulkPhotoTagger.lua's photo->tag matching.

Boundary cases mirror the semantics implied by ``isPhotoInTagTimeWindow`` /
``performPhotoTagMatching`` in ``BulkPhotoTagger.lua`` (inclusive both ends, open-ended
tag = start-only check, first match wins). Every boundary case is checked through
:func:`match_batch` so the entry point is exercised end to end for every scenario.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from teamgym_watcher.api import Competition, Tag
from teamgym_watcher.matcher import match_batch


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


def _match_single(capture_time: datetime, tags_sorted: list[Tag]) -> Tag | None:
    """Run match_batch on a single photo and return the result."""
    results = list(match_batch([("photo", capture_time)], tags_sorted))
    assert len(results) == 1
    _, matched = results[0]
    return matched


T0 = datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC)


class TestBoundarySemantics:
    def test_photo_exactly_at_tag_start_matches(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=30))

        assert _match_single(T0, [tag]) == tag

    def test_photo_exactly_at_tag_end_matches(self) -> None:
        end = T0 + timedelta(minutes=30)
        tag = _tag(1, T0, end)

        assert _match_single(end, [tag]) == tag

    def test_photo_between_tags_in_a_gap_does_not_match(self) -> None:
        tag_a = _tag(1, T0, T0 + timedelta(minutes=10))
        tag_b = _tag(2, T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
        gap_time = T0 + timedelta(minutes=15)

        assert _match_single(gap_time, [tag_a, tag_b]) is None

    def test_photo_before_all_tags_does_not_match(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))

        assert _match_single(T0 - timedelta(minutes=1), [tag]) is None

    def test_photo_after_all_closed_tags_does_not_match(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))

        assert _match_single(T0 + timedelta(minutes=11), [tag]) is None

    def test_photo_inside_open_ended_tag_matches(self) -> None:
        tag = _tag(1, T0, None)

        assert _match_single(T0 + timedelta(days=1), [tag]) == tag

    def test_photo_before_open_ended_tag_start_does_not_match(self) -> None:
        tag = _tag(1, T0, None)

        assert _match_single(T0 - timedelta(seconds=1), [tag]) is None

    def test_matches_second_tag_not_first_when_after_first_ends(self) -> None:
        tag_a = _tag(1, T0, T0 + timedelta(minutes=10))
        tag_b = _tag(2, T0 + timedelta(minutes=10, seconds=1), T0 + timedelta(minutes=20))
        photo_time = T0 + timedelta(minutes=15)

        assert _match_single(photo_time, [tag_a, tag_b]) == tag_b

    def test_no_tags_at_all_does_not_match(self) -> None:
        assert _match_single(T0, []) is None

    def test_touching_tags_at_shared_boundary_prefer_the_earlier_tag(self) -> None:
        """One phase's end_time can equal the next phase's start_time exactly (e.g.
        Qualification ending the instant Finals begins) -- the non-overlap guarantee
        doesn't rule this out. Both tags' windows contain that instant; the Lua's scan
        order means the earlier tag (encountered first) always wins the tie."""
        boundary = T0 + timedelta(minutes=10)
        tag_a = _tag(1, T0, boundary)
        tag_b = _tag(2, boundary, T0 + timedelta(minutes=20))

        assert _match_single(boundary, [tag_a, tag_b]) == tag_a


class TestNonUtcOffsetsCompareByInstant:
    def test_photo_in_different_offset_representing_same_instant_still_matches(self) -> None:
        tag = _tag(1, T0, T0 + timedelta(minutes=10))
        # T0 is 2026-07-20T10:00:00Z; expressed with a +02:00 offset that's the same instant.
        same_instant_plus_two = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert same_instant_plus_two.astimezone(UTC) == T0

        assert _match_single(same_instant_plus_two, [tag]) == tag


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
