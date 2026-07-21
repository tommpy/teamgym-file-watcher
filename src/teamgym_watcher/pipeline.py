"""Batch orchestration: extract capture time -> match -> map -> derive path -> move -> journal.

No Lua logic itself (like ``mover.py``/``watcher.py``) -- this module is the step-09 glue that
wires together steps 05-08 for one drained batch of :class:`~teamgym_watcher.mover.PhotoGroup`
objects, per PLAN.md's "Batching / API economy" and prompts/09.

Two kinds of failure are treated very differently within :func:`process_batch`:

- A per-group problem (unreadable EXIF, a move that fails) is isolated: it's logged, journaled
  as an error, and the batch continues with the next group -- one bad photo must never stop
  the watcher (CLAUDE.md).
- :class:`~teamgym_watcher.api.Unauthorized` from the per-batch ``fetch_tags_window`` call is
  not about one photo. It halts the batch immediately; all groups come back in
  :attr:`BatchResult.pending` so the caller (the watch loop) can pause consumption and retry
  the same groups once re-authenticated, rather than journaling them as errors and dropping work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from teamgym_watcher import exif, hierarchy, mapping, matcher, mover
from teamgym_watcher.api import Tag, Unauthorized
from teamgym_watcher.journal import JournalRecord, JournalStatus
from teamgym_watcher.mover import MoveStatus, PhotoGroup

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from teamgym_watcher.config import Config
    from teamgym_watcher.journal import Journal, Stats

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _TagWindowFetcher(Protocol):
    def fetch_tags_window(
        self, competition_id: int, start: datetime, end: datetime
    ) -> list[Tag]: ...


class CompetitionNameCache:
    """Remembers the competition's display name once any batch resolves it via a matched
    tag, so a later batch whose photos don't themselves match a tag can still build the
    normal Competitions/<name>/Random path instead of falling back to ``untagged``.
    """

    def __init__(self) -> None:
        self._name: str | None = None

    def observe(self, name: str | None) -> None:
        if name is not None:
            self._name = name

    @property
    def name(self) -> str | None:
        return self._name


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    """Everything :func:`process_batch` needs, all injectable so tests can supply fakes.

    ``api_client`` is called once per batch via ``fetch_tags_window`` scoped to that batch's
    own photos' capture-time range. An expired token surfaces as
    :class:`~teamgym_watcher.api.Unauthorized`, which pauses the entire batch -- see the
    module docstring for how that propagates out of :func:`process_batch`.
    """

    config: Config
    api_client: _TagWindowFetcher
    journal: Journal
    stats: Stats
    competition_name: CompetitionNameCache = field(default_factory=CompetitionNameCache)
    now: Callable[[], datetime] = _utc_now
    extract_capture_time: Callable[[Path, Config], exif.CaptureTime] = exif.extract_capture_time
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
    during the batch's ``fetch_tags_window`` call -- the caller must keep them queued and
    retry, never drop them.
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

    Per-batch strategy (PLAN.md "Batching / API economy"):
    1. Extract capture times for every group (EXIF errors mark a group as "no capture time").
    2. Issue one ``fetch_tags_window`` call scoped to the min/max capture time of this batch.
    3. Run ``match_batch`` against the returned tag list.
    4. For each group, resolve the destination and move/journal/log.

    An ``Unauthorized`` error from step 2 returns the entire batch as ``pending`` so the
    caller can pause and retry after re-authentication -- nothing is journaled as an error.
    Per-group exceptions in step 4 are isolated: one bad photo never stops the rest.
    """
    group_list = list(groups)

    # Step a: extract capture times
    # ExifError -> no-capture-time Random routing; any other Exception is isolated the same
    # way the old per-group loop was: it becomes an ERROR outcome, not a batch-level failure.
    timed: list[tuple[PhotoGroup, datetime | None, str]] = []
    exif_errors: dict[int, Exception] = {}
    for group in group_list:
        try:
            capture_time: datetime | None = deps.extract_capture_time(
                group.capture_time_file, deps.config
            ).utc
            timed.append((group, capture_time, ""))
        except exif.ExifError as exc:
            logger.warning("No capture time for %s: %s", group.capture_time_file, exc)
            timed.append((group, None, "no capture time"))
        except Exception as exc:
            exif_errors[id(group)] = exc
            timed.append((group, None, ""))  # handled in step e via exif_errors

    # Step b: one fetch for the whole batch, scoped to this batch's capture-time window
    groups_with_time: list[tuple[PhotoGroup, datetime]] = [
        (g, ct) for g, ct, _ in timed if ct is not None
    ]
    tags: list[Tag] = []
    if groups_with_time:
        window_start = min(ct for _, ct in groups_with_time)
        window_end = max(ct for _, ct in groups_with_time)
        try:
            tags = deps.api_client.fetch_tags_window(
                deps.config.competition_id, window_start, window_end
            )
        except Unauthorized:
            logger.debug(
                "Authentication required; pausing batch with %d group(s) pending", len(group_list)
            )
            return BatchResult(outcomes=(), pending=tuple(group_list), auth_paused=True)

    # Step d: match each group-with-capture-time against the fetched tags
    tag_matches: dict[int, Tag | None] = {}
    for group, matched_tag in matcher.match_batch(groups_with_time, tags):
        tag_matches[id(group)] = matched_tag

    # Step e: derive destination, move, journal, log -- one group at a time
    outcomes: list[GroupOutcome] = []
    for group, capture_time, reason in timed:
        # Non-ExifError failures from step a skip normal processing and become ERROR outcomes.
        exif_exc = exif_errors.get(id(group))
        if exif_exc is not None:
            outcomes.append(_record_error(group, exif_exc, deps))
            continue
        try:
            tag = tag_matches.get(id(group))
            if capture_time is not None and tag is None:
                reason = "no matching tag"

            metadata = deps.map_tag_to_metadata(tag).metadata if tag is not None else {}

            # Resolve competition name: matched tag wins, then cached name, then untagged
            if tag is not None and tag.competition.name is not None:
                comp_name = tag.competition.name
                deps.competition_name.observe(comp_name)
                dest_dir = deps.derive_destination_dir(deps.config.dest_root, metadata, comp_name)
            elif deps.competition_name.name is not None:
                dest_dir = deps.derive_destination_dir(
                    deps.config.dest_root, metadata, deps.competition_name.name
                )
            else:
                # No competition name has ever been resolved in this run -- escape hatch.
                # hierarchy.py is intentionally not involved here (CLAUDE.md: exact Lua port).
                dest_dir = deps.config.dest_root / "untagged"

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

            message = (
                "; ".join(move_errors) if move_errors else _status_message(status, tag, reason)
            )
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

            outcomes.append(GroupOutcome(group=group, status=status, tag=tag, dest_dir=dest_dir))
        except Exception as exc:  # one poison file must never stop the batch
            outcomes.append(_record_error(group, exc, deps))

    return BatchResult(outcomes=tuple(outcomes), pending=(), auth_paused=False)
