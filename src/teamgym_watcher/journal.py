"""Append-only JSONL idempotency journal, plus the shared stats counters for the periodic
summary line.

One record per :class:`~teamgym_watcher.mover.PhotoGroup`, appended to
``dest_root/.teamgym-watcher/journal.jsonl``. The journal is the sole restart-skip
mechanism (PLAN.md's "File handling": "already-journaled files are skipped on restart"),
so every outcome -- including errors -- is recorded and skipped next time; a permanently
broken file must not be retried forever on every restart.

Journal writes must never block a move: :meth:`Journal.append` catches and loudly logs
``OSError`` instead of raising, per prompts/08.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

JOURNAL_DIR_NAME: Final = ".teamgym-watcher"
JOURNAL_FILE_NAME: Final = "journal.jsonl"


class JournalStatus(Enum):
    """A group's final disposition, combining the physical move outcome with whether it
    matched a tag -- ``MOVED`` (matched a tag), ``RANDOM`` (unmatched, landed in a Random
    hierarchy tier), or ``ERROR`` (the move itself failed)."""

    MOVED = "moved"
    RANDOM = "random"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One journaled outcome for a photo group."""

    timestamp: datetime
    source_paths: tuple[Path, ...]
    dest_paths: tuple[Path, ...]
    tag_id: int | None
    metadata_applied: Mapping[str, str] | None
    status: JournalStatus
    message: str


def journal_path(dest_root: Path) -> Path:
    """The journal file location for a given destination root."""
    return dest_root / JOURNAL_DIR_NAME / JOURNAL_FILE_NAME


def _record_to_json(record: JournalRecord) -> str:
    payload: dict[str, object] = {
        "timestamp": record.timestamp.isoformat(),
        "source_paths": [str(p) for p in record.source_paths],
        "dest_paths": [str(p) for p in record.dest_paths],
        "tag_id": record.tag_id,
        "metadata_applied": dict(record.metadata_applied) if record.metadata_applied else None,
        "status": record.status.value,
        "message": record.message,
    }
    return json.dumps(payload, ensure_ascii=False)


class Journal:
    """Append-only JSONL journal at ``dest_root/.teamgym-watcher/journal.jsonl``."""

    def __init__(self, dest_root: Path) -> None:
        self._path = journal_path(dest_root)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: JournalRecord) -> None:
        """Append one record. Failures are logged loudly and swallowed -- a journal write
        must never block or fail a photo move."""
        line = _record_to_json(record)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            logger.error("Failed to write journal record to %s: %s", self._path, exc)

    def load_processed(self) -> set[str]:
        """Source file names (``Path.name``) already journaled, from any prior run, for the
        watcher to skip on restart -- regardless of their recorded status, so a file that
        errored last time is not retried forever on every restart."""
        processed: set[str] = set()
        if not self._path.is_file():
            return processed

        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                    except ValueError:
                        logger.warning("Skipping malformed journal line in %s", self._path)
                        continue
                    for source in data.get("source_paths", []):
                        if isinstance(source, str):
                            processed.add(Path(source).name)
        except OSError as exc:
            logger.error("Failed to read journal at %s: %s", self._path, exc)

        return processed


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    """A point-in-time read of :class:`Stats`, for the periodic summary log line."""

    processed: int
    matched: int
    random: int
    errors: int


class Stats:
    """Thread-safe processed/matched/random/errors counters, shared between the pipeline
    (which records each outcome) and a periodic logging thread (which reads a snapshot for
    the summary line)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processed = 0
        self._matched = 0
        self._random = 0
        self._errors = 0

    def record(self, status: JournalStatus) -> None:
        with self._lock:
            self._processed += 1
            if status is JournalStatus.MOVED:
                self._matched += 1
            elif status is JournalStatus.RANDOM:
                self._random += 1
            else:
                self._errors += 1

    def snapshot(self) -> StatsSnapshot:
        with self._lock:
            return StatsSnapshot(
                processed=self._processed,
                matched=self._matched,
                random=self._random,
                errors=self._errors,
            )
