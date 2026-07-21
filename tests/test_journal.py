"""Tests for journal.py: append-only JSONL journal, restart skip, and Stats counters."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from teamgym_watcher.journal import (
    Journal,
    JournalRecord,
    JournalStatus,
    Stats,
    journal_path,
)

T0 = datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC)


def _record(
    *,
    sources: tuple[Path, ...],
    dests: tuple[Path, ...],
    status: JournalStatus = JournalStatus.MOVED,
    tag_id: int | None = 42,
    metadata: dict[str, str] | None = None,
    message: str = "ok",
) -> JournalRecord:
    return JournalRecord(
        timestamp=T0,
        source_paths=sources,
        dest_paths=dests,
        tag_id=tag_id,
        metadata_applied=metadata,
        status=status,
        message=message,
    )


class TestJournalPath:
    def test_journal_path_is_under_dot_dir_in_dest_root(self, tmp_path: Path) -> None:
        assert journal_path(tmp_path) == tmp_path / ".teamgym-watcher" / "journal.jsonl"


class TestAppendAndRoundTrip:
    def test_append_writes_one_jsonl_line_with_expected_fields(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        source = tmp_path / "src" / "IMG_0001.JPG"
        dest = tmp_path / "dest" / "IMG_0001.JPG"
        record = _record(
            sources=(source,), dests=(dest,), metadata={"Phase": "Competition"}, tag_id=7
        )

        journal.append(record)

        lines = journal.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["source_paths"] == [str(source)]
        assert data["dest_paths"] == [str(dest)]
        assert data["tag_id"] == 7
        assert data["metadata_applied"] == {"Phase": "Competition"}
        assert data["status"] == "moved"
        assert data["message"] == "ok"
        assert data["timestamp"] == T0.isoformat()

    def test_multiple_appends_accumulate_as_separate_lines(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(_record(sources=(tmp_path / "a.jpg",), dests=(tmp_path / "d" / "a.jpg",)))
        journal.append(_record(sources=(tmp_path / "b.jpg",), dests=(tmp_path / "d" / "b.jpg",)))

        lines = journal.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_null_tag_id_and_metadata_round_trip_for_random_status(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        source = tmp_path / "src" / "IMG_0002.JPG"
        dest = tmp_path / "dest" / "Random" / "IMG_0002.JPG"
        journal.append(
            _record(
                sources=(source,),
                dests=(dest,),
                status=JournalStatus.RANDOM,
                tag_id=None,
                metadata=None,
                message="No matching tag",
            )
        )

        data = json.loads(journal.path.read_text(encoding="utf-8").splitlines()[0])
        assert data["tag_id"] is None
        assert data["metadata_applied"] is None
        assert data["status"] == "random"


class TestLoadProcessed:
    def test_returns_empty_set_when_no_journal_exists(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)

        assert journal.load_processed() == set()

    def test_returns_source_paths_from_prior_run(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(
            _record(
                sources=(tmp_path / "src" / "IMG_0001.CR3", tmp_path / "src" / "IMG_0001.JPG"),
                dests=(tmp_path / "dest" / "IMG_0001.CR3", tmp_path / "dest" / "IMG_0001.JPG"),
            )
        )

        processed = journal.load_processed()

        assert processed == {
            str(tmp_path / "src" / "IMG_0001.CR3"),
            str(tmp_path / "src" / "IMG_0001.JPG"),
        }

    def test_dedup_is_by_full_path_not_filename(self, tmp_path: Path) -> None:
        """Two distinct photos sharing a filename (different cameras, or a later card reusing
        a name) must each still be treated as unprocessed -- only the exact prior path is
        skipped, per prompts/09 ("dedup by path")."""
        journal = Journal(tmp_path)
        journal.append(
            _record(
                sources=(tmp_path / "card1" / "IMG_0001.JPG",),
                dests=(tmp_path / "dest" / "IMG_0001.JPG",),
            )
        )

        processed = journal.load_processed()

        assert str(tmp_path / "card1" / "IMG_0001.JPG") in processed
        assert str(tmp_path / "card2" / "IMG_0001.JPG") not in processed

    def test_restart_reads_records_appended_by_a_prior_journal_instance(
        self, tmp_path: Path
    ) -> None:
        first = Journal(tmp_path)
        first.append(
            _record(
                sources=(tmp_path / "src" / "IMG_0003.JPG",),
                dests=(tmp_path / "dest" / "IMG_0003.JPG",),
            )
        )

        second = Journal(tmp_path)
        assert second.load_processed() == {str(tmp_path / "src" / "IMG_0003.JPG")}

    def test_error_status_records_are_also_treated_as_processed(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(
            _record(
                sources=(tmp_path / "src" / "IMG_0004.JPG",),
                dests=(),
                status=JournalStatus.ERROR,
                tag_id=None,
                metadata=None,
                message="disk full",
            )
        )

        assert journal.load_processed() == {str(tmp_path / "src" / "IMG_0004.JPG")}

    def test_malformed_line_is_skipped_without_raising(self, tmp_path: Path) -> None:
        journal = Journal(tmp_path)
        journal.append(
            _record(
                sources=(tmp_path / "src" / "IMG_0005.JPG",),
                dests=(tmp_path / "dest" / "IMG_0005.JPG",),
            )
        )
        with journal.path.open("a", encoding="utf-8") as handle:
            handle.write("not valid json\n")

        processed = journal.load_processed()

        assert processed == {str(tmp_path / "src" / "IMG_0005.JPG")}


class TestAppendFailureDoesNotRaise:
    def test_append_swallows_oserror_when_directory_cannot_be_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blocking_file = tmp_path / "not-a-dir"
        blocking_file.write_text("x")
        journal = Journal(blocking_file)  # dest_root is a file, not a directory

        journal.append(
            _record(sources=(tmp_path / "a.jpg",), dests=(tmp_path / "dest" / "a.jpg",))
        )  # must not raise


class TestStats:
    def test_records_processed_matched_random_and_errors_independently(self) -> None:
        stats = Stats()

        stats.record(JournalStatus.MOVED)
        stats.record(JournalStatus.MOVED)
        stats.record(JournalStatus.RANDOM)
        stats.record(JournalStatus.ERROR)

        snapshot = stats.snapshot()
        assert snapshot.processed == 4
        assert snapshot.matched == 2
        assert snapshot.random == 1
        assert snapshot.errors == 1

    def test_fresh_stats_snapshot_is_all_zero(self) -> None:
        snapshot = Stats().snapshot()

        assert snapshot.processed == 0
        assert snapshot.matched == 0
        assert snapshot.random == 0
        assert snapshot.errors == 0
