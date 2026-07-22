"""Tests for CLI-only configuration building and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from teamgym_watcher.config import (
    LOCAL_API_BASE_URL,
    PROD_API_BASE_URL,
    STAGING_API_BASE_URL,
    Config,
    ConfigError,
    build_config,
    resolve_api_base_url,
)


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    return import_dir, dest_root


class TestResolveApiBaseUrl:
    def test_explicit_override_wins_even_with_local_and_staging_set(self) -> None:
        explicit = "https://custom.example/api"

        resolved = resolve_api_base_url(local=True, staging=True, api_base_url=explicit)

        assert resolved == explicit

    def test_local_only_uses_local_constant(self) -> None:
        resolved = resolve_api_base_url(local=True, staging=False, api_base_url=None)

        assert resolved == LOCAL_API_BASE_URL

    def test_staging_only_uses_staging_constant(self) -> None:
        resolved = resolve_api_base_url(local=False, staging=True, api_base_url=None)

        assert resolved == STAGING_API_BASE_URL

    def test_neither_flag_defaults_to_prod(self) -> None:
        resolved = resolve_api_base_url(local=False, staging=False, api_base_url=None)

        assert resolved == PROD_API_BASE_URL

    def test_local_and_staging_conflict_without_override_raises(self) -> None:
        with pytest.raises(ConfigError, match="cannot both be set"):
            resolve_api_base_url(local=True, staging=True, api_base_url=None)


class TestBuildConfig:
    def test_builds_with_defaults_for_optional_fields(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        cfg = build_config(
            api_base_url=LOCAL_API_BASE_URL,
            import_dir=import_dir,
            dest_root=dest_root,
        )

        assert cfg == Config(
            api_base_url=LOCAL_API_BASE_URL,
            import_dir=import_dir,
            dest_root=dest_root,
            timezone=None,
            time_offset_seconds=0,
            stability_seconds=3,
            batch_size=25,
            dry_run=False,
            exiftool_enabled=False,
            exiftool_path="exiftool",
            recursive=False,
        )

    def test_import_dir_must_exist(self, tmp_path: Path) -> None:
        _, dest_root = _dirs(tmp_path)
        missing_import_dir = tmp_path / "does-not-exist"

        with pytest.raises(ConfigError, match="does not exist"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=missing_import_dir,
                dest_root=dest_root,
            )

    def test_import_dir_must_be_a_directory(self, tmp_path: Path) -> None:
        _, dest_root = _dirs(tmp_path)
        import_file = tmp_path / "not-a-directory.txt"
        import_file.write_text("x", encoding="utf-8")

        with pytest.raises(ConfigError, match="not a directory"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=import_file,
                dest_root=dest_root,
            )

    def test_dest_root_not_creatable_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        def _raise(*args: object, **kwargs: object) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "mkdir", _raise)

        with pytest.raises(ConfigError, match="could not be created"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=import_dir,
                dest_root=dest_root,
            )

    def test_dest_root_inside_import_dir_rejected_without_creation(self, tmp_path: Path) -> None:
        import_dir, _ = _dirs(tmp_path)
        nested_dest = import_dir / "sorted"

        with pytest.raises(ConfigError, match="must not be inside"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=import_dir,
                dest_root=nested_dest,
            )

        assert not nested_dest.exists()

    def test_import_dir_inside_dest_root_rejected(self, tmp_path: Path) -> None:
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        nested_import = dest_root / "import"
        nested_import.mkdir()

        with pytest.raises(ConfigError, match="must not be inside"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=nested_import,
                dest_root=dest_root,
            )

    def test_bad_timezone_rejected(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        with pytest.raises(ConfigError, match="not a recognised IANA timezone"):
            build_config(
                api_base_url=LOCAL_API_BASE_URL,
                import_dir=import_dir,
                dest_root=dest_root,
                timezone="Not/AZone",
            )

    @pytest.mark.parametrize("field", ["stability_seconds", "batch_size"])
    def test_non_positive_intervals_rejected(self, tmp_path: Path, field: str) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        kwargs: dict[str, object] = {
            "api_base_url": LOCAL_API_BASE_URL,
            "import_dir": import_dir,
            "dest_root": dest_root,
            field: 0,
        }
        with pytest.raises(ConfigError, match="positive integer"):
            build_config(**kwargs)
