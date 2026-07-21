"""Companion-file grouping and collision-safe physical moves into the derived hierarchy.

No Lua equivalent -- the plugin never touches files on disk, so this module has no parity
requirement (unlike ``mapping.py``/``hierarchy.py``/``matcher.py``). Behaviour is driven
entirely by PLAN.md's "File handling" section and prompts/08.

Two independent pieces:

- :func:`group_files` -- groups files sharing a case-insensitive basename (RAW+JPEG pairs,
  pre-existing sidecars) so companions always move together.
- :func:`move_group` -- moves one group into a destination directory: collision-safe
  (whole-group ``-1``, ``-2``, ... suffixing so pairs stay aligned), and atomic per file
  (``Path.replace`` when possible, else copy+fsync+verify+replace+delete-source so a failure
  never loses or truncates a photo).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

RAW_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".raf", ".orf", ".rw2", ".dng"}
)
JPEG_EXTENSIONS: Final[frozenset[str]] = frozenset({".jpg", ".jpeg"})
SIDECAR_EXTENSIONS: Final[frozenset[str]] = frozenset({".xmp"})


@dataclass(frozen=True, slots=True)
class PhotoGroup:
    """Files sharing a case-insensitive basename within one directory, moved as a unit.

    ``files`` is sorted (RAW, then JPEG, then everything else, each alphabetically) so
    output is deterministic regardless of input order. ``capture_time_file`` is the file
    whose EXIF should supply the group's capture time -- RAW first, else JPEG, else
    whatever's left (e.g. an orphaned sidecar), per PLAN.md.
    """

    files: tuple[Path, ...]
    capture_time_file: Path


class MoveStatus(Enum):
    """Physical outcome of a :func:`move_group` call. Whether the destination happened to
    be a "Random" hierarchy tier is a pipeline-level concern (see ``journal.JournalStatus``),
    not something the mover itself knows about."""

    MOVED = "moved"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FileMoveResult:
    """Outcome for one file within a group. ``dest`` is ``None`` and ``error`` is set when
    that specific file's move failed; other files in the same group may still have
    succeeded."""

    source: Path
    dest: Path | None
    error: str | None


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Outcome of moving one :class:`PhotoGroup`. ``status`` is :attr:`MoveStatus.ERROR` if
    any file in the group failed, even when others succeeded."""

    group: PhotoGroup
    dest_dir: Path
    status: MoveStatus
    files: tuple[FileMoveResult, ...]
    dry_run: bool


def _extension_rank(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        return 0
    if suffix in JPEG_EXTENSIONS:
        return 1
    return 2


def _pick_capture_time_file(files: tuple[Path, ...]) -> Path:
    for path in files:
        if path.suffix.lower() in RAW_EXTENSIONS:
            return path
    for path in files:
        if path.suffix.lower() in JPEG_EXTENSIONS:
            return path
    return files[0]


def group_files(paths: Iterable[Path]) -> list[PhotoGroup]:
    """Group files sharing a case-insensitive stem within the same parent directory.

    Grouping is by (parent directory, stem) so companions are only merged when they sit
    side by side, exactly how a card ingest or import folder lays them out.
    """
    ordered_paths = sorted(paths, key=lambda p: p.name.casefold())

    buckets: dict[tuple[Path, str], list[Path]] = {}
    for path in ordered_paths:
        key = (path.parent, path.stem.casefold())
        buckets.setdefault(key, []).append(path)

    groups: list[PhotoGroup] = []
    for files in buckets.values():
        ordered_files = tuple(sorted(files, key=lambda p: (_extension_rank(p), p.name.casefold())))
        capture_time_file = _pick_capture_time_file(ordered_files)
        groups.append(PhotoGroup(files=ordered_files, capture_time_file=capture_time_file))

    groups.sort(key=lambda g: g.files[0].name.casefold())
    return groups


def _suffixed_name(name: str, suffix: int) -> str:
    if suffix == 0:
        return name
    parsed = PurePath(name)
    return f"{parsed.stem}-{suffix}{parsed.suffix}"


def _find_available_suffix(group: PhotoGroup, dest_dir: Path) -> int:
    suffix = 0
    while any((dest_dir / _suffixed_name(f.name, suffix)).exists() for f in group.files):
        suffix += 1
    return suffix


def _copy_then_replace(source: Path, dest: Path) -> None:
    """Cross-device fallback: copy to a temp file beside ``dest``, fsync, verify size, then
    an atomic same-volume replace -- only then is ``source`` deleted, so a failure at any
    point leaves it untouched."""
    tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
    try:
        with source.open("rb") as src_f, tmp.open("wb") as dst_f:
            shutil.copyfileobj(src_f, dst_f)
            dst_f.flush()
            os.fsync(dst_f.fileno())
        if tmp.stat().st_size != source.stat().st_size:
            raise OSError(f"Size mismatch copying {source} to {dest}: copy incomplete.")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    source.unlink()


def _move_one(source: Path, dest: Path) -> None:
    try:
        source.replace(dest)
    except OSError as exc:
        logger.debug(
            "Direct replace failed for %s -> %s (%s); falling back to copy", source, dest, exc
        )
        _copy_then_replace(source, dest)


def move_group(group: PhotoGroup, dest_dir: Path, *, dry_run: bool) -> MoveResult:
    """Move every file in ``group`` into ``dest_dir`` as a unit.

    Collision-safe: if any intended filename already exists in ``dest_dir``, the whole
    group is suffixed ``-1``, ``-2``, ... so companion files keep matching stems. Each
    file's move is independently atomic (see :func:`_move_one`); one file failing does not
    stop the others, but marks the group's status as :attr:`MoveStatus.ERROR`.
    """
    if dry_run:
        dry_run_results = tuple(
            FileMoveResult(source=f, dest=dest_dir / f.name, error=None) for f in group.files
        )
        for result in dry_run_results:
            logger.info("[dry-run] would move %s -> %s", result.source, result.dest)
        return MoveResult(
            group=group,
            dest_dir=dest_dir,
            status=MoveStatus.MOVED,
            files=dry_run_results,
            dry_run=True,
        )

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        message = f"Could not create destination directory {dest_dir}: {exc}"
        logger.error(message)
        error_results = tuple(
            FileMoveResult(source=f, dest=None, error=message) for f in group.files
        )
        return MoveResult(
            group=group,
            dest_dir=dest_dir,
            status=MoveStatus.ERROR,
            files=error_results,
            dry_run=False,
        )

    suffix = _find_available_suffix(group, dest_dir)
    move_results: list[FileMoveResult] = []
    had_error = False
    for f in group.files:
        dest = dest_dir / _suffixed_name(f.name, suffix)
        try:
            _move_one(f, dest)
            move_results.append(FileMoveResult(source=f, dest=dest, error=None))
            logger.info("Moved %s -> %s", f, dest)
        except OSError as exc:
            message = f"Failed to move {f} to {dest}: {exc}"
            logger.error(message)
            move_results.append(FileMoveResult(source=f, dest=None, error=message))
            had_error = True

    status = MoveStatus.ERROR if had_error else MoveStatus.MOVED
    return MoveResult(
        group=group, dest_dir=dest_dir, status=status, files=tuple(move_results), dry_run=False
    )
