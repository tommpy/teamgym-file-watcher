"""EXIF capture-time extraction: the photo is the source of truth for timezone.

Reads ``DateTimeOriginal`` (required) and ``OffsetTimeOriginal`` (EXIF 2.31, optional) via
``exifread``, which parses JPEG and TIFF-based RAW formats (NEF, CR2, ARW, DNG, ...)
uniformly. :data:`EXIFTOOL_ONLY_EXTENSIONS` documents the one RAW container exifread
cannot parse at all; those route to an ``exiftool`` subprocess fallback instead, gated by
``Config.exiftool_enabled`` so the external-binary dependency stays opt-in.

Timezone resolution order, most to least authoritative:

1. ``OffsetTimeOriginal`` from the photo itself (e.g. ``"+02:00"``) -- this is what the
   camera actually recorded for the shutter instant, so it overrides everything else.
2. ``Config.timezone`` (an IANA zone name) when the photo carries no offset.
3. The machine's local timezone, resolved for the photo's own date (so DST transitions
   are handled correctly), as a last resort.

``Config.time_offset_seconds`` (camera clock-skew correction) is applied after timezone
resolution, and the result is converted to aware UTC.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from json import loads as json_loads
from re import compile as re_compile
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import exifread

if TYPE_CHECKING:
    from pathlib import Path

    from teamgym_watcher.config import Config

logger = logging.getLogger(__name__)

# RAW containers that are not TIFF-based, so exifread cannot parse them at all (see
# PLAN.md's RAW-EXIF-coverage risk). Extend this set if another such format shows up.
EXIFTOOL_ONLY_EXTENSIONS: Final[frozenset[str]] = frozenset({".cr3"})

_DATETIME_TAG: Final = "EXIF DateTimeOriginal"
_OFFSET_TAG: Final = "EXIF OffsetTimeOriginal"
_EXIF_DATETIME_FORMAT: Final = "%Y:%m:%d %H:%M:%S"
_OFFSET_PATTERN: Final = re_compile(r"^([+-])(\d{2}):(\d{2})$")


class ExifError(Exception):
    """Base class for capture-time extraction failures."""


class MissingCaptureTimeError(ExifError):
    """No usable ``DateTimeOriginal`` tag was found.

    Contract: the pipeline catches this, routes the photo to the Random tier of the
    hierarchy, and journals a warning -- one photo with bad/missing EXIF must never stop
    the watcher.
    """


class ExifToolUnavailableError(ExifError):
    """The ``exiftool`` fallback is required but disabled, missing, or failed to run."""


class TimestampSource(Enum):
    """Which input determined a photo's timezone, for per-photo debug logging."""

    PHOTO_OFFSET = "photo_offset"
    CONFIG_TIMEZONE = "config_timezone"
    LOCAL_TIMEZONE = "local_timezone"


@dataclass(frozen=True, slots=True)
class CaptureTime:
    """A photo's capture time, resolved to an aware UTC ``datetime``."""

    utc: datetime
    source: TimestampSource


def ensure_exiftool_available(config: Config) -> None:
    """Startup check for the ``exiftool`` fallback: call once before processing any photos.

    A no-op when ``config.exiftool_enabled`` is ``False``. Otherwise raises
    :class:`ExifToolUnavailableError` with a message fit to show the user directly, so a
    misconfigured or missing binary is caught immediately rather than on the first RAW
    file partway through a run.
    """
    if not config.exiftool_enabled:
        return
    try:
        subprocess.run([config.exiftool_path, "-ver"], capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExifToolUnavailableError(
            f"exiftool_enabled is set but '{config.exiftool_path}' could not be run: {exc}. "
            "Install exiftool and/or set exiftool_path in the config."
        ) from exc


def extract_capture_time(path: Path, config: Config) -> CaptureTime:
    """Read a photo's capture time and resolve its timezone per the module-level order.

    Raises :class:`MissingCaptureTimeError` if the file has no ``DateTimeOriginal``, and
    :class:`ExifToolUnavailableError` if the file's extension requires the ``exiftool``
    fallback but it is disabled or unusable.
    """
    if path.suffix.lower() in EXIFTOOL_ONLY_EXTENSIONS:
        raw_datetime, raw_offset = _read_via_exiftool(path, config)
    else:
        raw_datetime, raw_offset = _read_via_exifread(path)

    if raw_datetime is None:
        raise MissingCaptureTimeError(f"No DateTimeOriginal tag in {path}")

    naive = _parse_exif_datetime(raw_datetime, path)
    aware, source = _localize(naive, raw_offset, config)

    if config.time_offset_seconds:
        aware += timedelta(seconds=config.time_offset_seconds)

    logger.debug("Capture time for %s: %s (timezone source: %s)", path, aware, source.value)
    return CaptureTime(utc=aware.astimezone(UTC), source=source)


def _read_via_exifread(path: Path) -> tuple[str | None, str | None]:
    with path.open("rb") as handle:
        tags = exifread.process_file(handle, details=False)
    raw_datetime = tags.get(_DATETIME_TAG)
    raw_offset = tags.get(_OFFSET_TAG)
    return (
        str(raw_datetime) if raw_datetime is not None else None,
        str(raw_offset) if raw_offset is not None else None,
    )


def _read_via_exiftool(path: Path, config: Config) -> tuple[str | None, str | None]:
    if not config.exiftool_enabled:
        raise ExifToolUnavailableError(
            f"'{path.suffix}' requires the exiftool fallback (exifread cannot parse it), "
            "but exiftool_enabled is not set in the config."
        )
    try:
        result = subprocess.run(
            [config.exiftool_path, "-j", "-DateTimeOriginal", "-OffsetTimeOriginal", str(path)],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExifToolUnavailableError(f"exiftool failed to read {path}: {exc}") from exc

    try:
        parsed = json_loads(result.stdout)
    except ValueError as exc:
        raise ExifToolUnavailableError(f"exiftool returned invalid JSON for {path}: {exc}") from exc
    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise ExifToolUnavailableError(
            f"exiftool returned unexpected output for {path}: {result.stdout!r}"
        )

    entry = parsed[0]
    raw_datetime = entry.get("DateTimeOriginal")
    raw_offset = entry.get("OffsetTimeOriginal")
    return (
        raw_datetime if isinstance(raw_datetime, str) else None,
        raw_offset if isinstance(raw_offset, str) else None,
    )


def _parse_exif_datetime(raw: str, path: Path) -> datetime:
    try:
        return datetime.strptime(raw, _EXIF_DATETIME_FORMAT)
    except ValueError as exc:
        raise MissingCaptureTimeError(f"Unparseable DateTimeOriginal {raw!r} in {path}") from exc


def _localize(
    naive: datetime, raw_offset: str | None, config: Config
) -> tuple[datetime, TimestampSource]:
    if raw_offset is not None:
        offset = _parse_offset(raw_offset)
        if offset is not None:
            return naive.replace(tzinfo=offset), TimestampSource.PHOTO_OFFSET
        logger.debug("Unparseable OffsetTimeOriginal %r; falling back", raw_offset)

    if config.timezone is not None:
        return naive.replace(tzinfo=ZoneInfo(config.timezone)), TimestampSource.CONFIG_TIMEZONE

    # A naive datetime's own .astimezone() presumes it represents local time and attaches
    # the system timezone, correctly resolved for DST at this specific date.
    return naive.astimezone(), TimestampSource.LOCAL_TIMEZONE


def _parse_offset(raw: str) -> timezone | None:
    if raw == "Z":
        return UTC
    match = _OFFSET_PATTERN.match(raw)
    if match is None:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(-delta if sign == "-" else delta)
