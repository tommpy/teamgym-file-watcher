"""Tests for watcher.py's queue/stability logic.

Per prompts/09, watchdog's real `Observer` is kept out of unit tests (integration covers it);
these tests exercise `StabilityQueue`, `scan_directory`, `is_watchable_file`, and `Watcher`'s
`rescan`/`poll_ready` directly, with an injectable clock so nothing sleeps for real.
"""

from __future__ import annotations

from pathlib import Path

from teamgym_watcher.watcher import (
    StabilityQueue,
    Watcher,
    is_watchable_file,
    scan_directory,
)


def _touch(path: Path, content: bytes = b"data") -> Path:
    path.write_bytes(content)
    return path


class TestIsWatchableFile:
    def test_jpeg_is_watchable(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "IMG_0001.JPG") is True

    def test_raw_is_watchable(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "IMG_0001.CR3") is True

    def test_sidecar_is_watchable(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "IMG_0001.xmp") is True

    def test_non_photo_extension_is_ignored(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "notes.txt") is False

    def test_dot_prefixed_hidden_file_is_ignored(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / ".IMG_0001.JPG") is False

    def test_tilde_prefixed_file_is_ignored(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "~IMG_0001.JPG") is False

    def test_tmp_suffix_is_ignored(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "IMG_0001.tmp") is False

    def test_extension_match_is_case_insensitive(self, tmp_path: Path) -> None:
        assert is_watchable_file(tmp_path / "IMG_0001.jpg") is True
        assert is_watchable_file(tmp_path / "IMG_0001.JPG") is True


class TestScanDirectory:
    def test_lists_watchable_files_non_recursive(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.jpg")
        _touch(tmp_path / "b.txt")

        found = scan_directory(tmp_path, recursive=False)

        assert found == [tmp_path / "a.jpg"]

    def test_ignores_subdirectory_by_default(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _touch(sub / "a.jpg")

        found = scan_directory(tmp_path, recursive=False)

        assert found == []

    def test_recursive_finds_files_in_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        nested = _touch(sub / "a.jpg")

        found = scan_directory(tmp_path, recursive=True)

        assert found == [nested]


class TestStabilityQueue:
    def test_freshly_enqueued_file_is_not_ready_on_first_poll(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])

        queue.enqueue(path)

        assert queue.poll_ready() == []

    def test_file_becomes_ready_once_size_is_stable_for_the_window(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])
        queue.enqueue(path)

        queue.poll_ready()  # first check: records the size, not ready yet
        clock[0] = 3.0

        assert queue.poll_ready() == [path]

    def test_ready_path_is_not_returned_twice(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])
        queue.enqueue(path)
        queue.poll_ready()
        clock[0] = 3.0
        assert queue.poll_ready() == [path]

        assert queue.poll_ready() == []

    def test_size_change_resets_the_stability_timer(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg", b"data")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])
        queue.enqueue(path)
        queue.poll_ready()

        clock[0] = 2.0
        path.write_bytes(b"more data now")  # still being written
        assert queue.poll_ready() == []  # size changed: timer reset, not ready

        clock[0] = 4.0  # only 2s since the reset -- still not stable for 3s
        assert queue.poll_ready() == []

        clock[0] = 5.0  # 3s since the reset
        assert queue.poll_ready() == [path]

    def test_disappeared_file_is_dropped_silently(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])
        queue.enqueue(path)
        queue.poll_ready()
        path.unlink()

        clock[0] = 3.0
        assert queue.poll_ready() == []
        assert len(queue) == 0

    def test_enqueue_is_deduplicated_by_path(self, tmp_path: Path) -> None:
        path = _touch(tmp_path / "a.jpg")
        queue = StabilityQueue(stability_seconds=3)

        queue.enqueue(path)
        queue.enqueue(path)

        assert len(queue) == 1

    def test_re_enqueue_after_ready_does_not_lose_the_size_reset(self, tmp_path: Path) -> None:
        # Re-enqueuing an already-tracked (not-yet-ready) path must not disturb its timer.
        path = _touch(tmp_path / "a.jpg")
        clock = [0.0]
        queue = StabilityQueue(stability_seconds=3, clock=lambda: clock[0])
        queue.enqueue(path)
        queue.poll_ready()

        clock[0] = 1.0
        queue.enqueue(path)  # duplicate enqueue mid-wait
        clock[0] = 3.0

        assert queue.poll_ready() == [path]


class TestWatcherRescan:
    def test_rescan_enqueues_files_present_on_disk(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.jpg")
        watcher = Watcher(tmp_path, stability_seconds=3)

        found = watcher.rescan(already_processed=set())

        assert found == 1
        assert len(watcher.queue) == 1

    def test_rescan_skips_already_journaled_names(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.jpg")
        watcher = Watcher(tmp_path, stability_seconds=3)

        found = watcher.rescan(already_processed={"a.jpg"})

        assert found == 0
        assert len(watcher.queue) == 0

    def test_rescan_dedups_against_already_tracked_paths(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.jpg")
        watcher = Watcher(tmp_path, stability_seconds=3)

        watcher.rescan(already_processed=set())
        found_second = watcher.rescan(already_processed=set())

        assert found_second == 0
        assert len(watcher.queue) == 1

    def test_rescan_picks_up_files_created_while_the_observer_was_down(
        self, tmp_path: Path
    ) -> None:
        # No Observer is ever started in this test -- files simply appear on disk (as they
        # would while the watcher process wasn't running) and a rescan must still find them.
        clock = [0.0]
        watcher = Watcher(tmp_path, stability_seconds=3, clock=lambda: clock[0])

        _touch(tmp_path / "late.jpg")
        watcher.rescan(already_processed=set())
        watcher.poll_ready()
        clock[0] = 3.0

        assert watcher.poll_ready() == [tmp_path / "late.jpg"]

    def test_recursive_rescan_finds_nested_files(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _touch(sub / "a.jpg")
        watcher = Watcher(tmp_path, recursive=True, stability_seconds=3)

        found = watcher.rescan(already_processed=set())

        assert found == 1
