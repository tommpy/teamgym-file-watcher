"""Tests for mover.py: companion grouping and collision-safe atomic moves."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from teamgym_watcher.mover import (
    MoveStatus,
    PhotoGroup,
    group_files,
    move_group,
)


def _touch(path: Path, content: bytes = b"data") -> Path:
    path.write_bytes(content)
    return path


class TestGroupFiles:
    def test_raw_and_jpeg_pair_form_one_group(self, tmp_path: Path) -> None:
        raw = _touch(tmp_path / "IMG_0001.CR3")
        jpeg = _touch(tmp_path / "IMG_0001.JPG")

        groups = group_files([raw, jpeg])

        assert len(groups) == 1
        assert set(groups[0].files) == {raw, jpeg}

    def test_solo_file_forms_its_own_group(self, tmp_path: Path) -> None:
        solo = _touch(tmp_path / "IMG_0002.JPG")

        groups = group_files([solo])

        assert groups == [PhotoGroup(files=(solo,), capture_time_file=solo)]

    def test_case_insensitive_stem_match_groups_together(self, tmp_path: Path) -> None:
        raw = _touch(tmp_path / "img_0001.cr3")
        jpeg = _touch(tmp_path / "IMG_0001.JPG")

        groups = group_files([raw, jpeg])

        assert len(groups) == 1
        assert set(groups[0].files) == {raw, jpeg}

    def test_different_stems_form_separate_groups(self, tmp_path: Path) -> None:
        a = _touch(tmp_path / "IMG_0001.JPG")
        b = _touch(tmp_path / "IMG_0002.JPG")

        groups = group_files([a, b])

        assert len(groups) == 2

    def test_different_directories_do_not_group_together(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        a = _touch(dir_a / "IMG_0001.JPG")
        b = _touch(dir_b / "IMG_0001.JPG")

        groups = group_files([a, b])

        assert len(groups) == 2

    def test_sidecar_joins_raw_jpeg_group(self, tmp_path: Path) -> None:
        raw = _touch(tmp_path / "IMG_0001.CR3")
        jpeg = _touch(tmp_path / "IMG_0001.JPG")
        xmp = _touch(tmp_path / "IMG_0001.xmp")

        groups = group_files([raw, jpeg, xmp])

        assert len(groups) == 1
        assert set(groups[0].files) == {raw, jpeg, xmp}

    def test_capture_time_file_prefers_raw_over_jpeg(self, tmp_path: Path) -> None:
        raw = _touch(tmp_path / "IMG_0001.CR3")
        jpeg = _touch(tmp_path / "IMG_0001.JPG")

        groups = group_files([jpeg, raw])

        assert groups[0].capture_time_file == raw

    def test_capture_time_file_falls_back_to_jpeg_when_no_raw(self, tmp_path: Path) -> None:
        jpeg = _touch(tmp_path / "IMG_0001.JPG")
        xmp = _touch(tmp_path / "IMG_0001.xmp")

        groups = group_files([xmp, jpeg])

        assert groups[0].capture_time_file == jpeg

    def test_unicode_filenames_group_correctly(self, tmp_path: Path) -> None:
        raw = _touch(tmp_path / "IMG_0001_éèü.CR3")
        jpeg = _touch(tmp_path / "IMG_0001_éèü.JPG")

        groups = group_files([raw, jpeg])

        assert len(groups) == 1
        assert set(groups[0].files) == {raw, jpeg}


class TestMoveGroupBasics:
    def test_moves_solo_file_into_destination(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest" / "nested"
        source_dir.mkdir()
        photo = _touch(source_dir / "IMG_0001.JPG")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.MOVED
        assert not photo.exists()
        assert (dest_dir / "IMG_0001.JPG").exists()
        assert result.files[0].dest == dest_dir / "IMG_0001.JPG"

    def test_moves_pair_together(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        raw = _touch(source_dir / "IMG_0001.CR3", b"raw-data")
        jpeg = _touch(source_dir / "IMG_0001.JPG", b"jpeg-data")
        group = PhotoGroup(files=(raw, jpeg), capture_time_file=raw)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.MOVED
        assert not raw.exists()
        assert not jpeg.exists()
        assert (dest_dir / "IMG_0001.CR3").read_bytes() == b"raw-data"
        assert (dest_dir / "IMG_0001.JPG").read_bytes() == b"jpeg-data"

    def test_dry_run_touches_nothing(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        photo = _touch(source_dir / "IMG_0001.JPG")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = move_group(group, dest_dir, dry_run=True)

        assert result.status is MoveStatus.MOVED
        assert result.dry_run is True
        assert photo.exists()
        assert not dest_dir.exists()


class TestCollisionSuffixing:
    def test_single_collision_suffixes_the_file(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        dest_dir.mkdir()
        _touch(dest_dir / "IMG_0001.JPG", b"existing")
        photo = _touch(source_dir / "IMG_0001.JPG", b"new")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.MOVED
        assert (dest_dir / "IMG_0001.JPG").read_bytes() == b"existing"
        assert (dest_dir / "IMG_0001-1.JPG").read_bytes() == b"new"

    def test_collision_suffix_keeps_pair_stems_aligned(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        dest_dir.mkdir()
        _touch(dest_dir / "IMG_0001.CR3", b"existing-raw")
        raw = _touch(source_dir / "IMG_0001.CR3", b"new-raw")
        jpeg = _touch(source_dir / "IMG_0001.JPG", b"new-jpeg")
        group = PhotoGroup(files=(raw, jpeg), capture_time_file=raw)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.MOVED
        assert (dest_dir / "IMG_0001-1.CR3").read_bytes() == b"new-raw"
        assert (dest_dir / "IMG_0001-1.JPG").read_bytes() == b"new-jpeg"

    def test_multiple_collisions_increment_suffix(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        dest_dir.mkdir()
        _touch(dest_dir / "IMG_0001.JPG")
        _touch(dest_dir / "IMG_0001-1.JPG")
        photo = _touch(source_dir / "IMG_0001.JPG", b"new")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        move_group(group, dest_dir, dry_run=False)

        assert (dest_dir / "IMG_0001-2.JPG").read_bytes() == b"new"


class TestCrossDeviceFallback:
    def test_replace_failure_falls_back_to_copy_and_still_moves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        photo = _touch(source_dir / "IMG_0001.JPG", b"payload")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        original_replace = Path.replace
        call_count = 0

        def flaky_replace(self: Path, target: Path) -> Path:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated cross-device failure")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.MOVED
        assert not photo.exists()
        assert (dest_dir / "IMG_0001.JPG").read_bytes() == b"payload"
        # No stray temp files left behind.
        assert list(dest_dir.iterdir()) == [dest_dir / "IMG_0001.JPG"]


class TestFailureLeavesSourceIntact:
    def test_permanent_replace_failure_reports_error_and_keeps_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        photo = _touch(source_dir / "IMG_0001.JPG", b"payload")
        group = PhotoGroup(files=(photo,), capture_time_file=photo)

        def always_fail_replace(self: Path, target: Path) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "replace", always_fail_replace)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.ERROR
        assert result.files[0].error is not None
        assert photo.exists()
        assert photo.read_bytes() == b"payload"

    def test_one_file_failing_does_not_abort_the_others_in_the_group(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both files are forced through the copy fallback (direct replace always fails,
        the fallback's own tmp->dest replace is left alone); the raw file's copy step is
        then made to fail permanently, while the jpeg's fallback succeeds normally."""
        source_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        source_dir.mkdir()
        raw = _touch(source_dir / "IMG_0001.CR3", b"raw")
        jpeg = _touch(source_dir / "IMG_0001.JPG", b"jpeg")
        group = PhotoGroup(files=(raw, jpeg), capture_time_file=raw)

        original_replace = Path.replace

        def force_fallback(self: Path, target: Path) -> Path:
            if self.suffix == ".tmp":
                return original_replace(self, target)
            raise OSError("simulated direct-replace failure")

        monkeypatch.setattr(Path, "replace", force_fallback)

        original_copyfileobj = shutil.copyfileobj

        def fail_copying_raw(src_f: object, dst_f: object) -> None:
            name = str(getattr(src_f, "name", ""))
            if name.endswith("IMG_0001.CR3"):
                raise OSError("simulated copy failure")
            original_copyfileobj(src_f, dst_f)

        monkeypatch.setattr(shutil, "copyfileobj", fail_copying_raw)

        result = move_group(group, dest_dir, dry_run=False)

        assert result.status is MoveStatus.ERROR
        assert raw.exists()
        assert not jpeg.exists()
        assert (dest_dir / "IMG_0001.JPG").exists()
