"""Tests for exif.py: capture-time extraction where the photo is the source of truth for
timezone.

Uses the real Lightroom-written JPEGs in ``samples/`` (their exact ``DateTimeOriginal``
values were confirmed directly via ``exifread`` — none carry ``OffsetTimeOriginal``, so
they exercise the config-timezone and local-timezone fallback paths) plus synthetic tiny
JPEGs built by :func:`_make_jpeg` for cases the samples can't cover: a photo-carried
offset, a missing timestamp, and DST/offset edge cases.
"""

from __future__ import annotations

import struct
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from teamgym_watcher.config import Config
from teamgym_watcher.exif import (
    EXIFTOOL_ONLY_EXTENSIONS,
    CaptureTime,
    ExifToolUnavailableError,
    MissingCaptureTimeError,
    TimestampSource,
    ensure_exiftool_available,
    extract_capture_time,
)

SAMPLES_DIR = Path(__file__).parent.parent / "samples"


def _config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "api_base_url": "http://localhost/api",
        "import_dir": Path(),
        "dest_root": Path(),
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _build_tiff(date_time_original: str | None, offset_time_original: str | None = None) -> bytes:
    """Hand-rolled minimal TIFF/EXIF blob: IFD0 -> ExifOffset -> Exif SubIFD with
    DateTimeOriginal and (optionally) OffsetTimeOriginal ASCII tags.
    """
    entries: list[tuple[int, str]] = []
    if date_time_original is not None:
        entries.append((0x9003, date_time_original))
    if offset_time_original is not None:
        entries.append((0x9011, offset_time_original))
    n = len(entries)

    ifd0_offset = 8
    ifd0_size = 2 + 12 * 1 + 4  # one entry: ExifOffset
    exif_ifd_offset = ifd0_offset + ifd0_size
    exif_ifd_size = 2 + 12 * n + 4
    data_offset = exif_ifd_offset + exif_ifd_size

    # TIFF rule: a value that fits in 4 bytes is stored inline in the entry itself; only
    # longer values are stored elsewhere with an offset pointer in the entry.
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
    return b"Exif\x00\x00" + header + ifd0 + exif_ifd + data_area


def _make_jpeg(
    path: Path,
    *,
    date_time_original: str | None,
    offset_time_original: str | None = None,
) -> Path:
    segments = b"\xff\xd8"  # SOI
    if date_time_original is not None or offset_time_original is not None:
        payload = _build_tiff(date_time_original, offset_time_original)
        segments += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    segments += b"\xff\xd9"  # EOI
    path.write_bytes(segments)
    return path


class TestSampleFixtures:
    """Real Lightroom-written JPEGs; expected local times taken from a direct exifread dump.

    None of the samples carry ``OffsetTimeOriginal``, so these exercise the fallback path
    (config timezone, or local machine zone) rather than the photo-offset path.
    """

    @pytest.mark.parametrize(
        ("filename", "expected_local"),
        [
            ("AWARD_Senior_Division_Champion_1784574391159.jpg", "2026-07-20T20:06:31"),
            ("Bohemian_FLOOR_1784574758672.jpg", "2026-07-20T20:12:38"),
            ("GENERAL_TRAVELLING_1784574693256.jpg", "2026-07-20T20:11:33"),
            ("photo_1784567438078.jpg", "2026-07-20T18:10:38"),
            ("Uppsala_Athletics_TUMBLE_1784574266783.jpg", "2026-07-20T20:04:26"),
        ],
    )
    def test_capture_time_with_config_timezone(self, filename: str, expected_local: str) -> None:
        config = _config(timezone="Europe/London")

        result = extract_capture_time(SAMPLES_DIR / filename, config)

        expected_utc = datetime.fromisoformat(expected_local).replace(
            tzinfo=ZoneInfo("Europe/London")
        )
        assert result.utc == expected_utc.astimezone(UTC)
        assert result.source is TimestampSource.CONFIG_TIMEZONE

    def test_capture_time_falls_back_to_local_timezone_when_no_config_timezone(self) -> None:
        config = _config(timezone=None)

        result = extract_capture_time(SAMPLES_DIR / "photo_1784567438078.jpg", config)

        naive = datetime(2026, 7, 20, 18, 10, 38)
        assert result.utc == naive.astimezone(UTC)
        assert result.source is TimestampSource.LOCAL_TIMEZONE


class TestPhotoOffsetOverridesConfigTimezone:
    def test_offset_time_original_wins_over_config_timezone(self, tmp_path: Path) -> None:
        photo = _make_jpeg(
            tmp_path / "offset.jpg",
            date_time_original="2026:07:20 20:06:31",
            offset_time_original="+02:00",
        )
        # Config says Europe/London (+01:00 in July), but the photo says +02:00.
        config = _config(timezone="Europe/London")

        result = extract_capture_time(photo, config)

        expected = datetime(2026, 7, 20, 20, 6, 31, tzinfo=timezone(timedelta(hours=2)))
        assert result.utc == expected.astimezone(UTC)
        assert result.source is TimestampSource.PHOTO_OFFSET

    def test_utc_offset_marker_z_is_recognised(self, tmp_path: Path) -> None:
        photo = _make_jpeg(
            tmp_path / "utc.jpg", date_time_original="2026:07:20 20:06:31", offset_time_original="Z"
        )
        config = _config(timezone="Europe/London")

        result = extract_capture_time(photo, config)

        assert result.utc == datetime(2026, 7, 20, 20, 6, 31, tzinfo=UTC)
        assert result.source is TimestampSource.PHOTO_OFFSET

    def test_unparseable_offset_falls_back_to_config_timezone(self, tmp_path: Path) -> None:
        photo = _make_jpeg(
            tmp_path / "bad_offset.jpg",
            date_time_original="2026:07:20 20:06:31",
            offset_time_original="not-an-offset",
        )
        config = _config(timezone="Europe/London")

        result = extract_capture_time(photo, config)

        expected = datetime(2026, 7, 20, 20, 6, 31, tzinfo=ZoneInfo("Europe/London"))
        assert result.utc == expected.astimezone(UTC)
        assert result.source is TimestampSource.CONFIG_TIMEZONE


class TestMissingDateTimeOriginal:
    def test_missing_datetime_original_raises_typed_error(self, tmp_path: Path) -> None:
        photo = _make_jpeg(tmp_path / "no_exif.jpg", date_time_original=None)
        config = _config()

        with pytest.raises(MissingCaptureTimeError):
            extract_capture_time(photo, config)

    def test_unparseable_datetime_original_raises_typed_error(self, tmp_path: Path) -> None:
        photo = _make_jpeg(tmp_path / "bad_date.jpg", date_time_original="not-a-date")
        config = _config()

        with pytest.raises(MissingCaptureTimeError):
            extract_capture_time(photo, config)


class TestTimeOffsetSeconds:
    def test_time_offset_seconds_shifts_the_result(self, tmp_path: Path) -> None:
        photo = _make_jpeg(
            tmp_path / "skewed.jpg",
            date_time_original="2026:07:20 20:06:31",
            offset_time_original="+00:00",
        )
        config = _config(time_offset_seconds=0)
        baseline = extract_capture_time(photo, config)

        skewed_config = _config(time_offset_seconds=-120)
        skewed = extract_capture_time(photo, skewed_config)

        assert skewed.utc == baseline.utc - timedelta(seconds=120)

    def test_time_offset_seconds_can_shift_a_boundary_match(self, tmp_path: Path) -> None:
        """A photo timestamped exactly one second after a tag's end boundary; the
        camera-skew correction pulls it back inside the window."""
        from teamgym_watcher.api import Competition, Tag
        from teamgym_watcher.matcher import match_batch

        tag_end = datetime(2026, 7, 20, 20, 6, 30, tzinfo=UTC)
        tag = Tag(
            id=1,
            competition_id=1,
            start_time=tag_end - timedelta(minutes=10),
            end_time=tag_end,
            section=None,
            age_group=None,
            phase=None,
            apparatus=None,
            team=None,
            group=None,
            award=None,
            competition=Competition(name="X", type="INTERNATIONAL"),
        )

        photo = _make_jpeg(
            tmp_path / "boundary.jpg",
            date_time_original="2026:07:20 20:06:31",  # one second after tag_end
            offset_time_original="+00:00",
        )

        unshifted = extract_capture_time(photo, _config(time_offset_seconds=0))
        ((_, unshifted_match),) = list(match_batch([("photo", unshifted.utc)], [tag]))
        assert unshifted_match is None

        shifted = extract_capture_time(photo, _config(time_offset_seconds=-1))
        ((_, shifted_match),) = list(match_batch([("photo", shifted.utc)], [tag]))
        assert shifted_match == tag


class TestDstTransition:
    def test_local_timezone_fallback_resolves_dst_correctly_either_side_of_transition(
        self, tmp_path: Path
    ) -> None:
        """Europe/London goes back from BST (+01:00) to GMT (+00:00) at 02:00 on the last
        Sunday of October 2026 (the 25th). 00:30 is unambiguously still BST; 03:00 is
        unambiguously GMT (both outside the 01:00-02:00 fold), so config.timezone must
        localise them to different UTC offsets."""
        before = _make_jpeg(tmp_path / "before_dst.jpg", date_time_original="2026:10:25 00:30:00")
        after = _make_jpeg(tmp_path / "after_dst.jpg", date_time_original="2026:10:25 03:00:00")
        config = _config(timezone="Europe/London")

        before_result = extract_capture_time(before, config)
        after_result = extract_capture_time(after, config)

        naive_before = datetime(2026, 10, 25, 0, 30, 0, tzinfo=ZoneInfo("Europe/London"))
        naive_after = datetime(2026, 10, 25, 3, 0, 0, tzinfo=ZoneInfo("Europe/London"))
        assert naive_before.utcoffset() == timedelta(hours=1)
        assert naive_after.utcoffset() == timedelta(hours=0)
        assert before_result.utc == naive_before.astimezone(UTC)
        assert after_result.utc == naive_after.astimezone(UTC)


class TestExiftoolFallback:
    def test_extension_requiring_exiftool_raises_when_disabled(self, tmp_path: Path) -> None:
        assert ".cr3" in EXIFTOOL_ONLY_EXTENSIONS
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=False)

        with pytest.raises(ExifToolUnavailableError):
            extract_capture_time(photo, config)

    def test_exiftool_process_failure_raises_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=True)

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, args)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ExifToolUnavailableError):
            extract_capture_time(photo, config)

    def test_extension_requiring_exiftool_uses_subprocess_when_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=True, exiftool_path="exiftool")

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert args[0] == "exiftool"
            assert str(photo) in args
            return subprocess.CompletedProcess(
                args,
                0,
                stdout='[{"DateTimeOriginal": "2026:07:20 20:06:31", '
                '"OffsetTimeOriginal": "+02:00"}]',
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = extract_capture_time(photo, config)

        expected = datetime(2026, 7, 20, 20, 6, 31, tzinfo=timezone(timedelta(hours=2)))
        assert result.utc == expected.astimezone(UTC)
        assert result.source is TimestampSource.PHOTO_OFFSET

    def test_exiftool_invalid_json_output_raises_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=True)

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, stdout="not json", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ExifToolUnavailableError):
            extract_capture_time(photo, config)

    def test_exiftool_empty_list_output_raises_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=True)

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ExifToolUnavailableError):
            extract_capture_time(photo, config)

    def test_exiftool_non_string_fields_are_treated_as_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        photo = tmp_path / "raw.cr3"
        photo.write_bytes(b"not a real cr3 file")
        config = _config(exiftool_enabled=True)

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args, 0, stdout='[{"DateTimeOriginal": 12345}]', stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(MissingCaptureTimeError):
            extract_capture_time(photo, config)

    def test_ensure_exiftool_available_is_a_noop_when_disabled(self) -> None:
        ensure_exiftool_available(_config(exiftool_enabled=False))

    def test_ensure_exiftool_available_raises_clear_error_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = _config(exiftool_enabled=True, exiftool_path="definitely-not-a-real-binary")

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("no such file")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(ExifToolUnavailableError, match="definitely-not-a-real-binary"):
            ensure_exiftool_available(config)


def test_capture_time_is_frozen() -> None:
    ct = CaptureTime(utc=datetime.now(UTC), source=TimestampSource.LOCAL_TIMEZONE)
    with pytest.raises(AttributeError):
        ct.utc = datetime.now(UTC)  # type: ignore[misc]
