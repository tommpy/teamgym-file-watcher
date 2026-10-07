"""Tests for pipeline.py's process_batch: happy path, Random routing, error isolation,
the per-batch fetch behaviour, the untagged fallback, and the authentication-pause contract,
all against fake deps (in-memory fake API client, tmp dirs)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from teamgym_watcher.api import ApiError, Competition, Tag, Unauthorized
from teamgym_watcher.config import Config
from teamgym_watcher.exif import CaptureTime, MissingCaptureTimeError, TimestampSource
from teamgym_watcher.journal import Journal, JournalStatus, Stats, journal_path
from teamgym_watcher.mover import PhotoGroup
from teamgym_watcher.pipeline import CompetitionNameCache, PipelineDeps, process_batch


def _touch(path: Path, content: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _tag(id_: int, hour: int, *, name: str = "Champs") -> Tag:
    return Tag(
        id=id_,
        competition_id=1,
        start_time=datetime(2024, 10, 5, hour, 0, 0, tzinfo=UTC),
        end_time=datetime(2024, 10, 5, hour + 1, 0, 0, tzinfo=UTC),
        section=None,
        age_group=None,
        phase=None,
        apparatus=None,
        team=None,
        group=None,
        award=None,
        competition=Competition(name=name, type=None),
    )


class FakeApiClient:
    """Stands in for ApiClient via structural typing (fetch_tags_window)."""

    def __init__(self, tags: list[Tag]) -> None:
        self._tags = tags
        self.calls: list[tuple[datetime, datetime]] = []

    def fetch_tags_window(self, start: datetime, end: datetime) -> list[Tag]:
        self.calls.append((start, end))
        return list(self._tags)


class UnauthorizedApiClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_tags_window(self, start: datetime, end: datetime) -> list[Tag]:
        self.calls += 1
        raise Unauthorized("token expired")


class ConnectionErrorApiClient:
    """Stands in for a server that's refusing connections, e.g. ``ConnectError`` after
    ``ApiClient``'s retry is exhausted."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_tags_window(self, start: datetime, end: datetime) -> list[Tag]:
        self.calls += 1
        raise ApiError("Request to http://localhost/api/tags failed after 2 attempt(s): boom")


def _config(tmp_path: Path, *, dry_run: bool = False) -> Config:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir(exist_ok=True)
    dest_root.mkdir(exist_ok=True)
    return Config(
        api_base_url="http://localhost/api",
        import_dir=import_dir,
        dest_root=dest_root,
        dry_run=dry_run,
    )


def _deps(
    tmp_path: Path,
    *,
    api_client: object | None = None,
    extract_capture_time: object,
    dry_run: bool = False,
    competition_name: CompetitionNameCache | None = None,
) -> PipelineDeps:
    config = _config(tmp_path, dry_run=dry_run)
    client = api_client if api_client is not None else FakeApiClient([_tag(1, 10)])
    return PipelineDeps(
        config=config,
        api_client=client,  # type: ignore[arg-type]
        journal=Journal(config.dest_root),
        stats=Stats(),
        competition_name=(
            competition_name if competition_name is not None else CompetitionNameCache()
        ),
        extract_capture_time=extract_capture_time,  # type: ignore[arg-type]
    )


def _fake_extractor(times: dict[Path, datetime | Exception]):
    def _extract(path: Path, config: Config) -> CaptureTime:
        result = times[path]
        if isinstance(result, Exception):
            raise result
        return CaptureTime(utc=result, source=TimestampSource.PHOTO_OFFSET)

    return _extract


class TestHappyPath:
    def test_matched_photo_is_moved_and_journaled_as_moved(self, tmp_path: Path) -> None:
        photo = _touch(tmp_path / "import" / "IMG_0001.JPG")
        matched_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor({photo: matched_time}),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        assert len(result.outcomes) == 1
        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.MOVED
        assert outcome.tag is not None and outcome.tag.id == 1
        assert not photo.exists()
        moved = tmp_path / "dest" / "Competitions" / "Champs" / "Random" / "IMG_0001.JPG"
        assert moved.exists()
        assert deps.stats.snapshot().matched == 1
        assert journal_path(deps.config.dest_root).is_file()

    def test_dry_run_leaves_the_source_file_untouched(self, tmp_path: Path) -> None:
        photo = _touch(tmp_path / "import" / "IMG_0001.JPG")
        matched_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor({photo: matched_time}),
            dry_run=True,
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        assert result.outcomes[0].status is JournalStatus.MOVED
        assert photo.exists()


class TestRandomRouting:
    def test_capture_time_outside_any_tag_window_routes_to_untagged_when_no_name_known(
        self, tmp_path: Path
    ) -> None:
        """With no competition name ever resolved, unmatched photos land in dest_root/untagged/."""
        photo = _touch(tmp_path / "import" / "IMG_0002.JPG")
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor({photo: outside_time}),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert outcome.tag is None
        assert not photo.exists()
        untagged = tmp_path / "dest" / "untagged" / "IMG_0002.JPG"
        assert untagged.exists()
        assert deps.stats.snapshot().random == 1

    def test_capture_time_outside_tag_window_routes_to_random_when_name_is_known(
        self, tmp_path: Path
    ) -> None:
        """Once a name is known from a matched tag, unmatched photos land in
        Competitions/<name>/Random/."""
        photo = _touch(tmp_path / "import" / "IMG_0002.JPG")
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        name_cache = CompetitionNameCache()
        name_cache.observe("Champs")
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor({photo: outside_time}),
            competition_name=name_cache,
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert outcome.tag is None
        moved = tmp_path / "dest" / "Competitions" / "Champs" / "Random" / "IMG_0002.JPG"
        assert moved.exists()

    def test_missing_capture_time_routes_to_untagged_when_no_name_known(
        self, tmp_path: Path
    ) -> None:
        photo = _touch(tmp_path / "import" / "IMG_0003.JPG")
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor(
                {photo: MissingCaptureTimeError("no DateTimeOriginal")}
            ),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert not photo.exists()
        untagged = tmp_path / "dest" / "untagged" / "IMG_0003.JPG"
        assert untagged.exists()
        assert deps.stats.snapshot().random == 1
        assert deps.stats.snapshot().errors == 0


class TestErrorIsolation:
    def test_one_poison_file_does_not_stop_the_rest_of_the_batch(self, tmp_path: Path) -> None:
        good_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        poison = _touch(tmp_path / "import" / "poison.JPG")
        good = _touch(tmp_path / "import" / "good.JPG")
        name_cache = CompetitionNameCache()
        name_cache.observe("Champs")
        deps = _deps(
            tmp_path,
            api_client=FakeApiClient([_tag(1, 10)]),
            extract_capture_time=_fake_extractor(
                {poison: RuntimeError("disk fell over"), good: good_time}
            ),
            competition_name=name_cache,
        )
        poison_group = PhotoGroup(files=(poison,), capture_time_file=poison)
        good_group = PhotoGroup(files=(good,), capture_time_file=good)

        result = process_batch([poison_group, good_group], deps)

        assert len(result.outcomes) == 2
        assert result.outcomes[0].status is JournalStatus.ERROR
        assert result.outcomes[1].status is JournalStatus.MOVED
        assert poison.exists()  # the failed file is left exactly where it was
        assert not good.exists()
        snapshot = deps.stats.snapshot()
        assert snapshot.errors == 1
        assert snapshot.matched == 1


class TestPerBatchFetch:
    def test_fetch_window_is_min_max_of_groups_with_capture_time(self, tmp_path: Path) -> None:
        """fetch_tags_window receives exactly [min, max] over the groups that have a
        capture time; groups without a capture time do not affect the window."""
        early = datetime(2024, 10, 5, 8, 0, tzinfo=UTC)
        late = datetime(2024, 10, 5, 14, 0, tzinfo=UTC)
        no_exif = _touch(tmp_path / "import" / "no_exif.JPG")
        p_early = _touch(tmp_path / "import" / "early.JPG")
        p_late = _touch(tmp_path / "import" / "late.JPG")
        api_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor(
                {
                    no_exif: MissingCaptureTimeError("no exif"),
                    p_early: early,
                    p_late: late,
                }
            ),
        )
        groups = [
            PhotoGroup(files=(no_exif,), capture_time_file=no_exif),
            PhotoGroup(files=(p_early,), capture_time_file=p_early),
            PhotoGroup(files=(p_late,), capture_time_file=p_late),
        ]

        process_batch(groups, deps)

        assert len(api_client.calls) == 1
        window_start, window_end = api_client.calls[0]
        assert window_start == early
        assert window_end == late

    def test_fetch_not_called_when_all_groups_lack_capture_time(self, tmp_path: Path) -> None:
        photos = [_touch(tmp_path / "import" / f"{n}.JPG") for n in range(3)]
        api_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor(
                {p: MissingCaptureTimeError("no exif") for p in photos}
            ),
        )
        groups = [PhotoGroup(files=(p,), capture_time_file=p) for p in photos]

        process_batch(groups, deps)

        assert api_client.calls == []

    def test_all_photos_match_a_tag(self, tmp_path: Path) -> None:
        photos = [_touch(tmp_path / "import" / f"{n}.JPG") for n in range(3)]
        matched_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        api_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor({p: matched_time for p in photos}),
        )
        groups = [PhotoGroup(files=(p,), capture_time_file=p) for p in photos]

        result = process_batch(groups, deps)

        assert len(api_client.calls) == 1  # one fetch for the whole batch
        assert all(o.status is JournalStatus.MOVED for o in result.outcomes)
        assert all(o.tag is not None for o in result.outcomes)

    def test_mix_of_matched_and_unmatched_in_one_batch(self, tmp_path: Path) -> None:
        p_matched = _touch(tmp_path / "import" / "matched.JPG")
        p_unmatched = _touch(tmp_path / "import" / "unmatched.JPG")
        matched_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        name_cache = CompetitionNameCache()
        api_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor(
                {
                    p_matched: matched_time,
                    p_unmatched: outside_time,
                }
            ),
            competition_name=name_cache,
        )
        g_matched = PhotoGroup(files=(p_matched,), capture_time_file=p_matched)
        g_unmatched = PhotoGroup(files=(p_unmatched,), capture_time_file=p_unmatched)

        result = process_batch([g_matched, g_unmatched], deps)

        assert len(api_client.calls) == 1
        statuses = [o.status for o in result.outcomes]
        assert JournalStatus.MOVED in statuses
        assert JournalStatus.RANDOM in statuses

    def test_some_groups_fail_exif_fetch_still_fires_for_the_rest(self, tmp_path: Path) -> None:
        good = _touch(tmp_path / "import" / "good.JPG")
        bad = _touch(tmp_path / "import" / "bad.JPG")
        good_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        api_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor(
                {
                    good: good_time,
                    bad: MissingCaptureTimeError("no exif"),
                }
            ),
        )
        g_good = PhotoGroup(files=(good,), capture_time_file=good)
        g_bad = PhotoGroup(files=(bad,), capture_time_file=bad)

        process_batch([g_good, g_bad], deps)

        # fetch is called using only the good group's time as both start and end
        assert len(api_client.calls) == 1
        window_start, window_end = api_client.calls[0]
        assert window_start == good_time
        assert window_end == good_time

    def test_untagged_fallback_when_no_competition_name_ever_resolved(self, tmp_path: Path) -> None:
        """When no tag has ever matched in this run, photos land in dest_root/untagged/."""
        photo = _touch(tmp_path / "import" / "IMG_0001.JPG")
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        api_client = FakeApiClient([_tag(1, 10)])  # tag for 10-11h, photo at 23h -> no match
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor({photo: outside_time}),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert (tmp_path / "dest" / "untagged" / "IMG_0001.JPG").exists()
        assert deps.competition_name.name is None

    def test_name_persists_across_batches(self, tmp_path: Path) -> None:
        """Batch N matches a tag and resolves the competition name. Batch N+1 has only
        unmatched photos but must still land under Competitions/<name>/Random, not untagged."""
        p1 = _touch(tmp_path / "import" / "batch1.JPG")
        p2 = _touch(tmp_path / "import" / "batch2.JPG")
        matched_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        api_client = FakeApiClient([_tag(1, 10)])
        competition_name = CompetitionNameCache()
        deps = _deps(
            tmp_path,
            api_client=api_client,
            extract_capture_time=_fake_extractor({p1: matched_time, p2: outside_time}),
            competition_name=competition_name,
        )

        # Batch N: p1 matches tag 1 -> resolves "Champs"
        g1 = PhotoGroup(files=(p1,), capture_time_file=p1)
        r1 = process_batch([g1], deps)
        assert r1.outcomes[0].status is JournalStatus.MOVED
        assert competition_name.name == "Champs"

        # Batch N+1: p2 does not match, but name is still remembered
        g2 = PhotoGroup(files=(p2,), capture_time_file=p2)
        r2 = process_batch([g2], deps)
        assert r2.outcomes[0].status is JournalStatus.RANDOM
        random_path = tmp_path / "dest" / "Competitions" / "Champs" / "Random" / "batch2.JPG"
        assert random_path.exists()
        assert not (tmp_path / "dest" / "untagged" / "batch2.JPG").exists()


class TestAuthenticationPause:
    def test_unauthorized_from_fetch_pauses_whole_batch_including_no_capture_time_groups(
        self, tmp_path: Path
    ) -> None:
        """An Unauthorized from fetch_tags_window halts the entire batch: every group
        (including those with no capture time) goes to pending, nothing is journaled."""
        photo_a = _touch(tmp_path / "import" / "a.JPG")
        photo_b = _touch(tmp_path / "import" / "b.JPG")  # will have no capture time
        deps = _deps(
            tmp_path,
            api_client=UnauthorizedApiClient(),
            extract_capture_time=_fake_extractor(
                {
                    photo_a: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                    photo_b: MissingCaptureTimeError("no exif"),
                }
            ),
        )
        group_a = PhotoGroup(files=(photo_a,), capture_time_file=photo_a)
        group_b = PhotoGroup(files=(photo_b,), capture_time_file=photo_b)

        result = process_batch([group_a, group_b], deps)

        assert result.auth_paused is True
        assert result.outcomes == ()
        assert result.pending == (group_a, group_b)
        assert photo_a.exists()
        assert photo_b.exists()
        assert not journal_path(deps.config.dest_root).exists()

    def test_unauthorized_pauses_batch_with_all_capture_time_groups_pending(
        self, tmp_path: Path
    ) -> None:
        photo_a = _touch(tmp_path / "import" / "a.JPG")
        photo_b = _touch(tmp_path / "import" / "b.JPG")
        deps = _deps(
            tmp_path,
            api_client=UnauthorizedApiClient(),
            extract_capture_time=_fake_extractor(
                {
                    photo_a: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                    photo_b: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                }
            ),
        )
        group_a = PhotoGroup(files=(photo_a,), capture_time_file=photo_a)
        group_b = PhotoGroup(files=(photo_b,), capture_time_file=photo_b)

        result = process_batch([group_a, group_b], deps)

        assert result.auth_paused is True
        assert result.outcomes == ()
        assert result.pending == (group_a, group_b)
        assert not journal_path(deps.config.dest_root).exists()


class TestConnectionErrorPause:
    """A network-level failure (server down, connection refused, timeout) must pause the
    batch for retry exactly like an expired token -- never crash the caller, never journal
    the groups as errors."""

    def test_api_error_from_fetch_pauses_whole_batch_and_is_not_journaled(
        self, tmp_path: Path
    ) -> None:
        photo_a = _touch(tmp_path / "import" / "a.JPG")
        photo_b = _touch(tmp_path / "import" / "b.JPG")
        deps = _deps(
            tmp_path,
            api_client=ConnectionErrorApiClient(),
            extract_capture_time=_fake_extractor(
                {
                    photo_a: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                    photo_b: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                }
            ),
        )
        group_a = PhotoGroup(files=(photo_a,), capture_time_file=photo_a)
        group_b = PhotoGroup(files=(photo_b,), capture_time_file=photo_b)

        result = process_batch([group_a, group_b], deps)

        assert result.auth_paused is False
        assert result.connection_error is True
        assert result.connection_message is not None
        assert "boom" in result.connection_message
        assert result.outcomes == ()
        assert result.pending == (group_a, group_b)
        assert photo_a.exists()
        assert photo_b.exists()
        assert not journal_path(deps.config.dest_root).exists()
