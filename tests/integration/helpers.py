"""Shared builders for the end-to-end integration tests in this package.

Not a test module itself -- pytest only collects ``test_*.py`` files, so these builders
(synthetic JPEG/RAW bytes, tag JSON, fake paginated ``/tags`` responses) live here to keep
the actual test files focused on scenario setup and assertions.
"""

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

SAMPLES_DIR = Path(__file__).parent.parent.parent / "samples"


def utc(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def local_to_utc(local_iso: str, zone: str = "Europe/London") -> datetime:
    """Convert a naive local ISO string (as read off a Lightroom metadata panel) to UTC,
    resolving DST for that specific date -- mirrors how :mod:`teamgym_watcher.exif` localises
    a photo's ``DateTimeOriginal`` against ``config.timezone`` when no offset is embedded."""
    return datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(zone)).astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# --- Tag JSON builders, matching the /tags response shape parsed by teamgym_watcher.api ---


def team_json(
    *, club_name: str | None = None, team_name: str | None = None, nationality: str | None = None
) -> dict[str, Any]:
    return {
        "id": 1,
        "clubName": club_name,
        "teamName": team_name,
        "nationality": nationality,
        "clubId": None,
    }


def group_json(*, name: str | None = None, round_type: str | None = None) -> dict[str, Any]:
    return {"id": 1, "name": name, "roundType": round_type}


def award_json(name: str) -> dict[str, Any]:
    return {"id": 1, "name": name}


def tag_json(
    *,
    id: int,
    competition_id: int,
    competition_name: str,
    competition_type: str,
    start: datetime,
    end: datetime | None = None,
    section: str | None = None,
    age_group: str | None = None,
    phase: str | None = None,
    apparatus: str | None = None,
    team: dict[str, Any] | None = None,
    group: dict[str, Any] | None = None,
    award: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "startTime": _iso(start),
        "endTime": _iso(end) if end is not None else None,
        "section": section,
        "ageGroup": age_group,
        "phase": phase,
        "apparatus": apparatus,
        "competition": {"id": competition_id, "name": competition_name, "type": competition_type},
        "team": team,
        "group": group,
        "award": award,
    }


def paginated_tags_callback(tags: list[dict[str, Any]], *, page_size: int = 2) -> Any:
    """A pytest-httpx callback serving ``tags`` across real pages of ``page_size``, regardless
    of the ``limit`` the client actually requested -- exercising
    :meth:`ApiClient.fetch_tags_window` pagination loop for real, the way a server enforcing
    its own page cap would. Query parameters other than ``page`` are ignored here; this
    callback is only responsible for exercising pagination assembly, not simulating real
    server-side time-window filtering."""

    def _callback(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        start = (page - 1) * page_size
        chunk = tags[start : start + page_size]
        has_next = start + page_size < len(tags)
        return httpx.Response(
            200, json={"data": chunk, "meta": {"page": page, "hasNext": has_next}}
        )

    return _callback


# --- Synthetic photo/RAW bytes with controlled EXIF ---


def _tiff_exif_bytes(date_time_original: str | None, offset_time_original: str | None) -> bytes:
    """Minimal TIFF/EXIF blob: IFD0 -> ExifOffset -> Exif SubIFD with DateTimeOriginal and
    (optionally) OffsetTimeOriginal ASCII tags. Same technique as tests/test_exif.py."""
    entries: list[tuple[int, str]] = []
    if date_time_original is not None:
        entries.append((0x9003, date_time_original))
    if offset_time_original is not None:
        entries.append((0x9011, offset_time_original))
    n = len(entries)

    ifd0_offset = 8
    ifd0_size = 2 + 12 * 1 + 4
    exif_ifd_offset = ifd0_offset + ifd0_size
    exif_ifd_size = 2 + 12 * n + 4
    data_offset = exif_ifd_offset + exif_ifd_size

    data_area = b""
    entry_specs: list[tuple[int, int, int, bytes]] = []
    cursor = data_offset
    for tag, value in entries:
        raw = value.encode("ascii") + b"\x00"
        if len(raw) <= 4:
            entry_specs.append((tag, 2, len(raw), raw.ljust(4, b"\x00")))
        else:
            entry_specs.append((tag, 2, len(raw), struct.pack("<I", cursor)))
            data_area += raw
            cursor += len(raw)

    ifd0 = struct.pack("<H", 1)
    ifd0 += struct.pack("<HHII", 0x8769, 4, 1, exif_ifd_offset)
    ifd0 += struct.pack("<I", 0)

    exif_ifd = struct.pack("<H", n)
    for tag, typ, count, value_field in entry_specs:
        exif_ifd += struct.pack("<HHI", tag, typ, count) + value_field
    exif_ifd += struct.pack("<I", 0)

    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd0_offset)
    return header + ifd0 + exif_ifd + data_area


def make_jpeg(
    path: Path,
    *,
    date_time_original: str | None,
    offset_time_original: str | None = None,
) -> Path:
    """A tiny valid JPEG carrying (or, if ``date_time_original`` is ``None``, entirely
    lacking) EXIF ``DateTimeOriginal``/``OffsetTimeOriginal``."""
    segments = b"\xff\xd8"  # SOI
    if date_time_original is not None or offset_time_original is not None:
        payload = b"Exif\x00\x00" + _tiff_exif_bytes(date_time_original, offset_time_original)
        segments += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    segments += b"\xff\xd9"  # EOI
    path.write_bytes(segments)
    return path


def make_raw(
    path: Path,
    *,
    date_time_original: str,
    offset_time_original: str | None = None,
) -> Path:
    """A fake RAW companion file: not a real camera format, but (like real RAW containers,
    which are TIFF-based) a bare TIFF/EXIF blob exifread can read directly -- readable fake
    bytes are enough to prove RAW+JPEG companion grouping and moving without shipping a real
    multi-megabyte RAW fixture."""
    path.write_bytes(_tiff_exif_bytes(date_time_original, offset_time_original))
    return path


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def list_tree(
    root: Path, *, ignore_dirs: frozenset[str] = frozenset({".teamgym-watcher"})
) -> set[str]:
    """Every file under ``root``, as ``/``-joined paths relative to ``root`` -- stable across
    OSes for snapshot comparison."""
    found: set[str] = set()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in ignore_dirs for part in path.relative_to(root).parts):
            continue
        found.add(path.relative_to(root).as_posix())
    return found
