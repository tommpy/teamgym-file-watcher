"""Tests for pipeline.py's process_batch: happy path, Random routing, error isolation, and
the authentication-pause behaviour, all against fake deps (in-memory tag cache, tmp dirs)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from teamgym_watcher.api import Competition, Tag, Unauthorized
from teamgym_watcher.config import Config
from teamgym_watcher.exif import CaptureTime, MissingCaptureTimeError, TimestampSource
from teamgym_watcher.journal import Journal, JournalStatus, Stats, journal_path
from teamgym_watcher.mover import PhotoGroup
from teamgym_watcher.pipeline import PipelineDeps, process_batch
from teamgym_watcher.tag_cache import TagCache


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
    """Stands in for ApiClient via structural typing, like tests/test_tag_cache.py's fake."""

    def __init__(self, tags: list[Tag]) -> None:
        self._tags = tags
        self.calls = 0

    def fetch_all_tags(self, competition_id: int) -> list[Tag]:
        self.calls += 1
        return list(self._tags)


class UnauthorizedApiClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_all_tags(self, competition_id: int) -> list[Tag]:
        self.calls += 1
        raise Unauthorized("token expired")


def _config(tmp_path: Path, *, dry_run: bool = False) -> Config:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir(exist_ok=True)
    dest_root.mkdir(exist_ok=True)
    return Config(
        api_base_url="http://localhost/api",
        competition_id=1,
        import_dir=import_dir,
        dest_root=dest_root,
        dry_run=dry_run,
    )


def _deps(
    tmp_path: Path,
    *,
    initial_tags: list[Tag] | None = None,
    clock_box: list[float] | None = None,
    api_client: object | None = None,
    extract_capture_time: object,
    dry_run: bool = False,
) -> PipelineDeps:
    box = clock_box if clock_box is not None else [100.0]
    config = _config(tmp_path, dry_run=dry_run)
    cache = TagCache(config.competition_id, tag_refresh_seconds=60, clock=lambda: box[0])
    cache.refresh(FakeApiClient(initial_tags if initial_tags is not None else [_tag(1, 10)]))
    client = api_client if api_client is not None else FakeApiClient([_tag(1, 10)])
    return PipelineDeps(
        config=config,
        tag_cache=cache,
        api_client=client,  # type: ignore[arg-type]
        journal=Journal(config.dest_root),
        stats=Stats(),
        clock=lambda: box[0],
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
            initial_tags=[_tag(1, 10)],
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
            initial_tags=[_tag(1, 10)],
            extract_capture_time=_fake_extractor({photo: matched_time}),
            dry_run=True,
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        assert result.outcomes[0].status is JournalStatus.MOVED
        assert photo.exists()


class TestRandomRouting:
    def test_capture_time_outside_any_tag_window_routes_to_random(self, tmp_path: Path) -> None:
        photo = _touch(tmp_path / "import" / "IMG_0002.JPG")
        outside_time = datetime(2024, 10, 5, 23, 0, tzinfo=UTC)
        deps = _deps(
            tmp_path,
            initial_tags=[_tag(1, 10)],
            extract_capture_time=_fake_extractor({photo: outside_time}),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert outcome.tag is None
        assert deps.stats.snapshot().random == 1

    def test_missing_capture_time_routes_to_random_without_erroring(self, tmp_path: Path) -> None:
        photo = _touch(tmp_path / "import" / "IMG_0003.JPG")
        deps = _deps(
            tmp_path,
            initial_tags=[_tag(1, 10)],
            extract_capture_time=_fake_extractor(
                {photo: MissingCaptureTimeError("no DateTimeOriginal")}
            ),
        )
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = process_batch([group], deps)

        outcome = result.outcomes[0]
        assert outcome.status is JournalStatus.RANDOM
        assert not photo.exists()  # still physically moved, just to the Random tier
        assert deps.stats.snapshot().random == 1
        assert deps.stats.snapshot().errors == 0


class TestErrorIsolation:
    def test_one_poison_file_does_not_stop_the_rest_of_the_batch(self, tmp_path: Path) -> None:
        good_time = datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
        poison = _touch(tmp_path / "import" / "poison.JPG")
        good = _touch(tmp_path / "import" / "good.JPG")
        deps = _deps(
            tmp_path,
            initial_tags=[_tag(1, 10)],
            extract_capture_time=_fake_extractor(
                {poison: RuntimeError("disk fell over"), good: good_time}
            ),
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


class TestTagCacheRefresh:
    def test_stale_cache_is_refreshed_at_most_once_per_batch(self, tmp_path: Path) -> None:
        clock_box = [100.0]
        refresh_client = FakeApiClient([_tag(1, 10)])
        deps = _deps(
            tmp_path,
            initial_tags=[_tag(1, 10)],
            clock_box=clock_box,
            api_client=refresh_client,
            extract_capture_time=_fake_extractor(
                {
                    tmp_path / "import" / f"{n}.JPG": datetime(2024, 10, 5, 10, 30, tzinfo=UTC)
                    for n in range(3)
                }
            ),
        )
        clock_box[0] = 200.0  # now past the 60s refresh window, set up by _deps at t=100
        groups = []
        for n in range(3):
            photo = _touch(tmp_path / "import" / f"{n}.JPG")
            groups.append(PhotoGroup(files=(photo,), capture_time_file=photo))

        process_batch(groups, deps)

        assert refresh_client.calls == 1


class TestAuthenticationPause:
    def test_unauthorized_refresh_pauses_the_batch_and_keeps_groups_pending(
        self, tmp_path: Path
    ) -> None:
        clock_box = [100.0]
        photo_a = _touch(tmp_path / "import" / "a.JPG")
        photo_b = _touch(tmp_path / "import" / "b.JPG")
        deps = _deps(
            tmp_path,
            initial_tags=[_tag(1, 10)],
            clock_box=clock_box,
            api_client=UnauthorizedApiClient(),
            extract_capture_time=_fake_extractor(
                {
                    photo_a: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                    photo_b: datetime(2024, 10, 5, 10, 30, tzinfo=UTC),
                }
            ),
        )
        clock_box[0] = 200.0  # now past the 60s refresh window, forcing a refresh attempt
        group_a = PhotoGroup(files=(photo_a,), capture_time_file=photo_a)
        group_b = PhotoGroup(files=(photo_b,), capture_time_file=photo_b)

        result = process_batch([group_a, group_b], deps)

        assert result.auth_paused is True
        assert result.outcomes == ()
        assert result.pending == (group_a, group_b)
        assert photo_a.exists()
        assert photo_b.exists()
        assert not journal_path(deps.config.dest_root).exists()
