"""Tests for TagCache: staleness and atomic swap under a concurrent reader. No real sleeps."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from teamgym_watcher.api import Competition, Tag
from teamgym_watcher.tag_cache import TagCache


def _tag(id_: int, hour: int, *, competition_name: str | None = "X") -> Tag:
    return Tag(
        id=id_,
        competition_id=1,
        start_time=datetime(2024, 10, 5, hour, 0, 0, tzinfo=UTC),
        end_time=None,
        section=None,
        age_group=None,
        phase=None,
        apparatus=None,
        team=None,
        group=None,
        award=None,
        competition=Competition(name=competition_name, type="INTERNATIONAL"),
    )


class FakeApiClient:
    """Stands in for :class:`~teamgym_watcher.api.ApiClient` in tests via structural typing."""

    def __init__(self, tags: list[Tag], *, ready: threading.Event | None = None) -> None:
        self._tags = tags
        self._ready = ready

    def fetch_all_tags(self, competition_id: int) -> list[Tag]:
        if self._ready is not None:
            self._ready.wait(timeout=5)
        return list(self._tags)


class TestStale:
    def test_stale_before_any_refresh(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)

        assert cache.stale(now=0.0) is True

    def test_not_stale_within_refresh_window(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60, clock=lambda: 100.0)

        cache.refresh(FakeApiClient([]))

        assert cache.stale(now=100.0) is False
        assert cache.stale(now=159.0) is False

    def test_stale_once_refresh_interval_elapses(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60, clock=lambda: 100.0)

        cache.refresh(FakeApiClient([]))

        assert cache.stale(now=160.0) is True


class TestRefresh:
    def test_refresh_populates_tags_sorted(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        tags = [_tag(1, 10), _tag(2, 11)]

        cache.refresh(FakeApiClient(tags))

        assert cache.tags_sorted == tags

    def test_tags_sorted_returns_a_copy_not_the_internal_list(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(FakeApiClient([_tag(1, 10)]))

        snapshot = cache.tags_sorted
        snapshot.append(_tag(99, 12))

        assert [tag.id for tag in cache.tags_sorted] == [1]

    def test_refresh_replaces_previous_tags_entirely(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(FakeApiClient([_tag(1, 10)]))

        cache.refresh(FakeApiClient([_tag(2, 11)]))

        assert [tag.id for tag in cache.tags_sorted] == [2]


class TestConcurrentAccess:
    def test_reader_is_not_blocked_while_a_refresh_fetch_is_in_progress(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(FakeApiClient([_tag(1, 10)]))

        release = threading.Event()
        slow_client = FakeApiClient([_tag(2, 11)], ready=release)

        refresh_thread = threading.Thread(target=cache.refresh, args=(slow_client,))
        refresh_thread.start()
        try:
            # The fetch is blocked on `release`, but reads must still see the old snapshot
            # immediately rather than waiting on the in-flight fetch.
            assert [tag.id for tag in cache.tags_sorted] == [1]
        finally:
            release.set()
            refresh_thread.join(timeout=5)

        assert [tag.id for tag in cache.tags_sorted] == [2]

    def test_reader_never_observes_a_torn_swap(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        old_tags = [_tag(1, 10), _tag(2, 11)]
        new_tags = [_tag(3, 12), _tag(4, 13), _tag(5, 14)]
        cache.refresh(FakeApiClient(old_tags))

        observed_lengths: set[int] = set()
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                observed_lengths.add(len(cache.tags_sorted))

        reader_thread = threading.Thread(target=reader)
        reader_thread.start()
        try:
            for _ in range(50):
                cache.refresh(FakeApiClient(old_tags))
                cache.refresh(FakeApiClient(new_tags))
        finally:
            stop.set()
            reader_thread.join(timeout=5)

        assert observed_lengths <= {len(old_tags), len(new_tags)}


class TestCompetitionName:
    def test_none_before_any_refresh(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)

        assert cache.competition_name is None

    def test_returns_name_from_first_named_tag(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(FakeApiClient([_tag(1, 10, competition_name="World Champs")]))

        assert cache.competition_name == "World Champs"

    def test_falls_back_to_a_later_tag_when_the_first_has_no_name(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(
            FakeApiClient(
                [_tag(1, 10, competition_name=None), _tag(2, 11, competition_name="World Champs")]
            )
        )

        assert cache.competition_name == "World Champs"

    def test_none_when_no_cached_tag_has_a_name(self) -> None:
        cache = TagCache(1, tag_refresh_seconds=60)
        cache.refresh(FakeApiClient([_tag(1, 10, competition_name=None)]))

        assert cache.competition_name is None
