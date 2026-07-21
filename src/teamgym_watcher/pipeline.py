"""Batch orchestration: extract capture time -> match -> map -> derive path -> move -> journal.

No Lua logic itself (like ``mover.py``/``watcher.py``) -- this module is the step-09 glue that
wires together steps 05-08 for one drained batch of :class:`~teamgym_watcher.mover.PhotoGroup`
objects, per PLAN.md's "Batching / API economy" and prompts/09.

Two kinds of failure are treated very differently within :func:`process_batch`:

- A per-group problem (unreadable EXIF, a move that fails) is isolated: it's logged, journaled
  as an error, and the batch continues with the next group -- one bad photo must never stop
  the watcher (CLAUDE.md).
- :class:`~teamgym_watcher.auth.AuthenticationRequired` is not about one photo. It halts the
  batch immediately; the untried groups come back in :attr:`BatchResult.pending` so the caller
  (the watch loop) can pause consumption and retry the same groups once re-authenticated,
  rather than journaling them as errors and dropping the work.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from teamgym_watcher import exif, hierarchy, mapping, matcher, mover
from teamgym_watcher.api import Tag, Unauthorized
from teamgym_watcher.auth import AuthenticationRequired
from teamgym_watcher.journal import JournalRecord, JournalStatus
from teamgym_watcher.mover import MoveStatus, PhotoGroup
from teamgym_watcher.tag_cache import TagCache, _TagFetcher

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from teamgym_watcher.config import Config
    from teamgym_watcher.journal import Journal, Stats

logger = logging.getLogger(__name__)

UNKNOWN_COMPETITION_NAME: Final = "Unknown Competition"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    """Everything :func:`process_batch` needs, all injectable so tests can supply fakes.

    ``api_client`` is whatever the caller currently holds a fresh token for; a stale tag
    cache is refreshed through it (single-flight, guarded by ``refresh_lock``). An expired
    token surfaces from that refresh as :class:`~teamgym_watcher.api.Unauthorized`, which is
    re-raised here as :class:`~teamgym_watcher.auth.AuthenticationRequired` -- see the module
    docstring for how that propagates out of :func:`process_batch`.
    """

    config: Config
    tag_cache: TagCache
    api_client: _TagFetcher
    journal: Journal
    stats: Stats
    refresh_lock: threading.Lock = field(default_factory=threading.Lock)
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = _utc_now
    extract_capture_time: Callable[[Path, Config], exif.CaptureTime] = exif.extract_capture_time
    match_one: Callable[[datetime, TagCache], Tag | None] = matcher.match_one
    map_tag_to_metadata: Callable[[Tag], mapping.MapResult] = mapping.map_tag_to_metadata
    derive_destination_dir: Callable[[Path, dict[str, str], str], Path] = (
        hierarchy.derive_destination_dir
    )
    move_group: Callable[..., mover.MoveResult] = mover.move_group


@dataclass(frozen=True, slots=True)
class GroupOutcome:
    """The final disposition of one :class:`PhotoGroup` after a :func:`process_batch` pass."""

    group: PhotoGroup
    status: JournalStatus
    tag: Tag | None
    dest_dir: Path | None


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Outcome of one :func:`process_batch` call.

    ``pending`` holds groups that were never attempted because authentication was required
    partway through the batch -- the caller must keep them queued and retry, never drop them.
    """

    outcomes: tuple[GroupOutcome, ...]
    pending: tuple[PhotoGroup, ...]
    auth_paused: bool


def format_summary_line(
    processed: int, matched: int, random_: int, errors: int, queued: int
) -> str:
    """The one-line stats format shared by the periodic summary and the batch-completion log."""
    return (
        f"processed={processed} matched={matched} random={random_} errors={errors} queued={queued}"
    )


def _maybe_refresh_tag_cache(deps: PipelineDeps) -> None:
    """Refresh the shared tag cache if stale, at most one refresh in flight at a time.

    If another refresh is already running, this returns immediately and processing continues
    against the current (possibly still-stale) snapshot -- staleness just means "due for a
    refresh", not "unusable".
    """
    if not deps.tag_cache.stale(deps.clock()):
        return
    if not deps.refresh_lock.acquire(blocking=False):
        return
    try:
        deps.tag_cache.refresh(deps.api_client)
    except Unauthorized as exc:
        raise AuthenticationRequired(f"Tag refresh was rejected as unauthenticated: {exc}") from exc
    finally:
        deps.refresh_lock.release()


def _status_message(status: JournalStatus, tag: Tag | None, reason: str) -> str:
    if status is JournalStatus.MOVED:
        assert tag is not None
        return f"Matched tag#{tag.id}"
    return f"Random: {reason or 'no matching tag'}"


def _log_group_outcome(
    group: PhotoGroup,
    tag: Tag | None,
    metadata: dict[str, str],
    dest_dir: Path,
    dest_root: Path,
    reason: str,
) -> None:
    name = group.files[0].name
    try:
        rel = dest_dir.relative_to(dest_root)
    except ValueError:
        rel = dest_dir

    if tag is not None:
        phase = metadata.get("Phase", "-")
        team = (
            metadata.get("TeamName")
            or metadata.get("ClubName")
            or metadata.get("Nationality")
            or "-"
        )
        apparatus = metadata.get("Apparatus", "-")
        logger.info("%s -> tag#%d (%s/%s/%s) -> %s", name, tag.id, phase, team, apparatus, rel)
    else:
        logger.info("%s -> Random (%s) -> %s", name, reason or "no matching tag", rel)


def _process_one_group(group: PhotoGroup, deps: PipelineDeps) -> GroupOutcome:
    capture_time: datetime | None = None
    reason = ""
    try:
        capture_time = deps.extract_capture_time(group.capture_time_file, deps.config).utc
    except exif.ExifError as exc:
        reason = "no capture time"
        logger.warning("No capture time for %s: %s", group.capture_time_file, exc)

    _maybe_refresh_tag_cache(deps)

    tag: Tag | None = None
    if capture_time is not None:
        tag = deps.match_one(capture_time, deps.tag_cache)
        if tag is None:
            reason = "no matching tag"

    metadata = deps.map_tag_to_metadata(tag).metadata if tag is not None else {}
    competition_name = deps.tag_cache.competition_name or UNKNOWN_COMPETITION_NAME
    dest_dir = deps.derive_destination_dir(deps.config.dest_root, metadata, competition_name)

    move_result = deps.move_group(group, dest_dir, dry_run=deps.config.dry_run)

    if move_result.status is MoveStatus.ERROR:
        status = JournalStatus.ERROR
    elif tag is not None:
        status = JournalStatus.MOVED
    else:
        status = JournalStatus.RANDOM

    dest_paths = tuple(f.dest for f in move_result.files if f.dest is not None)
    move_errors = [f.error for f in move_result.files if f.error is not None]

    _log_group_outcome(group, tag, metadata, dest_dir, deps.config.dest_root, reason)

    message = "; ".join(move_errors) if move_errors else _status_message(status, tag, reason)
    record = JournalRecord(
        timestamp=deps.now(),
        source_paths=group.files,
        dest_paths=dest_paths,
        tag_id=tag.id if tag is not None else None,
        metadata_applied=metadata or None,
        status=status,
        message=message,
    )
    deps.journal.append(record)
    deps.stats.record(status)

    return GroupOutcome(group=group, status=status, tag=tag, dest_dir=dest_dir)


def _record_error(group: PhotoGroup, exc: Exception, deps: PipelineDeps) -> GroupOutcome:
    name = group.files[0].name
    logger.error("Failed to process %s: %s", name, exc)
    logger.debug("Traceback for %s:", name, exc_info=exc)

    message = f"{type(exc).__name__}: {exc}"
    record = JournalRecord(
        timestamp=deps.now(),
        source_paths=group.files,
        dest_paths=(),
        tag_id=None,
        metadata_applied=None,
        status=JournalStatus.ERROR,
        message=message,
    )
    deps.journal.append(record)
    deps.stats.record(JournalStatus.ERROR)
    return GroupOutcome(group=group, status=JournalStatus.ERROR, tag=None, dest_dir=None)


def process_batch(groups: Iterable[PhotoGroup], deps: PipelineDeps) -> BatchResult:
    """Process one drained batch of :class:`PhotoGroup` objects end to end.

    See the module docstring for how per-group errors and authentication pauses are handled
    differently.
    """
    group_list = list(groups)
    outcomes: list[GroupOutcome] = []

    for index, group in enumerate(group_list):
        try:
            outcome = _process_one_group(group, deps)
        except AuthenticationRequired:
            remaining = len(group_list) - index
            logger.debug(
                "Authentication required; pausing batch with %d group(s) pending", remaining
            )
            return BatchResult(
                outcomes=tuple(outcomes), pending=tuple(group_list[index:]), auth_paused=True
            )
        except Exception as exc:  # one poison file must never stop the batch
            outcome = _record_error(group, exc, deps)
        outcomes.append(outcome)

    return BatchResult(outcomes=tuple(outcomes), pending=(), auth_paused=False)
