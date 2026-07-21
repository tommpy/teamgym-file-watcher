"""Tests for TOML config loading, CLI-override precedence, and validation."""

from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path

import pytest

from teamgym_watcher.config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config

EXAMPLE_TOML_PATH = Path(__file__).parent.parent / "teamgym-watcher.example.toml"

VALID_TOML = """
api_base_url = "http://localhost:3001/api"
competition_id = 1
import_dir = "{import_dir}"
dest_root = "{dest_root}"
timezone = "Europe/London"
time_offset_seconds = -5
tag_refresh_seconds = 30
stability_seconds = 2
batch_size = 10
dry_run = true
"""


def _write_config(tmp_path: Path, import_dir: Path, dest_root: Path) -> Path:
    config_path = tmp_path / "teamgym-watcher.toml"
    config_path.write_text(
        VALID_TOML.format(import_dir=import_dir.as_posix(), dest_root=dest_root.as_posix()),
        encoding="utf-8",
    )
    return config_path


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    return import_dir, dest_root


def _to_toml(values: dict[str, object]) -> str:
    """Minimal str/int/bool-only TOML serialiser, for round-tripping a parsed example file."""
    lines = []
    for key, value in values.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f'{key} = "{value}"')
    return "\n".join(lines)


class TestValidToml:
    def test_parses_all_fields(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = _write_config(tmp_path, import_dir, dest_root)

        cfg = load_config(config_path=config_path)

        assert cfg == Config(
            api_base_url="http://localhost:3001/api",
            competition_id=1,
            import_dir=import_dir,
            dest_root=dest_root,
            timezone="Europe/London",
            time_offset_seconds=-5,
            tag_refresh_seconds=30,
            stability_seconds=2,
            batch_size=10,
            dry_run=True,
        )

    def test_dest_root_is_created_if_missing(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = _write_config(tmp_path, import_dir, dest_root)

        load_config(config_path=config_path)

        assert dest_root.is_dir()

    def test_defaults_applied_when_optional_fields_omitted(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = tmp_path / "teamgym-watcher.toml"
        config_path.write_text(
            f"""
            api_base_url = "http://localhost:3001/api"
            competition_id = 1
            import_dir = "{import_dir.as_posix()}"
            dest_root = "{dest_root.as_posix()}"
            """,
            encoding="utf-8",
        )

        cfg = load_config(config_path=config_path)

        assert cfg.timezone is None
        assert cfg.time_offset_seconds == 0
        assert cfg.tag_refresh_seconds == 60
        assert cfg.stability_seconds == 3
        assert cfg.batch_size == 25
        assert cfg.dry_run is False
        assert cfg.recursive is False

    def test_recursive_flag_is_parsed_from_toml(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = tmp_path / "teamgym-watcher.toml"
        config_path.write_text(
            f"""
            api_base_url = "http://localhost:3001/api"
            competition_id = 1
            import_dir = "{import_dir.as_posix()}"
            dest_root = "{dest_root.as_posix()}"
            recursive = true
            """,
            encoding="utf-8",
        )

        cfg = load_config(config_path=config_path)

        assert cfg.recursive is True

    def test_missing_default_config_file_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / DEFAULT_CONFIG_FILENAME).exists()

        cfg = load_config(
            config_path=None,
            overrides={
                "api_base_url": "http://localhost:3001/api",
                "competition_id": 1,
                "import_dir": import_dir,
                "dest_root": dest_root,
            },
        )

        assert cfg.competition_id == 1

    def test_default_config_file_in_cwd_is_used_when_no_config_path_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        _write_config(tmp_path, import_dir, dest_root)
        monkeypatch.chdir(tmp_path)

        cfg = load_config(config_path=None)

        assert cfg.competition_id == 1


class TestInvalidToml:
    def test_malformed_toml_raises_actionable_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / "teamgym-watcher.toml"
        config_path.write_text("this is not [ valid toml", encoding="utf-8")

        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(config_path=config_path)

    def test_explicit_missing_config_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(config_path=tmp_path / "nope.toml")


class TestOverridePrecedence:
    def test_cli_override_wins_over_toml_value(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = _write_config(tmp_path, import_dir, dest_root)

        cfg = load_config(config_path=config_path, overrides={"competition_id": 99})

        assert cfg.competition_id == 99

    def test_none_override_does_not_replace_toml_value(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        config_path = _write_config(tmp_path, import_dir, dest_root)

        cfg = load_config(config_path=config_path, overrides={"competition_id": None})

        assert cfg.competition_id == 1

    def test_overrides_alone_can_satisfy_required_fields(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        cfg = load_config(
            config_path=None,
            overrides={
                "api_base_url": "http://localhost:3001/api",
                "competition_id": 7,
                "import_dir": import_dir,
                "dest_root": dest_root,
            },
        )

        assert cfg.import_dir == import_dir
        assert cfg.dest_root == dest_root


class TestValidationErrors:
    def _overrides(self, import_dir: Path, dest_root: Path, **extra: object) -> dict[str, object]:
        base: dict[str, object] = {
            "api_base_url": "http://localhost:3001/api",
            "competition_id": 1,
            "import_dir": import_dir,
            "dest_root": dest_root,
        }
        base.update(extra)
        return base

    def test_missing_required_field_names_the_field_and_cli_flag(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        overrides = self._overrides(import_dir, dest_root)
        del overrides["competition_id"]

        with pytest.raises(ConfigError, match=r"competition_id.*--competition-id"):
            load_config(config_path=None, overrides=overrides)

    def test_import_dir_must_exist(self, tmp_path: Path) -> None:
        _, dest_root = _dirs(tmp_path)
        missing = tmp_path / "does-not-exist"

        with pytest.raises(ConfigError, match="does not exist"):
            load_config(config_path=None, overrides=self._overrides(missing, dest_root))

    def test_import_dir_must_be_a_directory(self, tmp_path: Path) -> None:
        _, dest_root = _dirs(tmp_path)
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("x", encoding="utf-8")

        with pytest.raises(ConfigError, match="not a directory"):
            load_config(config_path=None, overrides=self._overrides(file_path, dest_root))

    def test_dest_root_must_not_be_an_existing_file(self, tmp_path: Path) -> None:
        import_dir, _ = _dirs(tmp_path)
        file_path = tmp_path / "dest-is-a-file.txt"
        file_path.write_text("x", encoding="utf-8")

        with pytest.raises(ConfigError, match="not a directory"):
            load_config(config_path=None, overrides=self._overrides(import_dir, file_path))

    def test_dest_root_inside_import_dir_rejected(self, tmp_path: Path) -> None:
        import_dir, _ = _dirs(tmp_path)
        nested_dest = import_dir / "sorted"

        with pytest.raises(ConfigError, match="must not be inside"):
            load_config(config_path=None, overrides=self._overrides(import_dir, nested_dest))

    def test_dest_root_inside_import_dir_is_not_created_before_rejection(
        self, tmp_path: Path
    ) -> None:
        import_dir, _ = _dirs(tmp_path)
        nested_dest = import_dir / "sorted"

        with pytest.raises(ConfigError, match="must not be inside"):
            load_config(config_path=None, overrides=self._overrides(import_dir, nested_dest))

        assert not nested_dest.exists()

    def test_import_dir_inside_dest_root_rejected(self, tmp_path: Path) -> None:
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        nested_import = dest_root / "import"
        nested_import.mkdir()

        with pytest.raises(ConfigError, match="must not be inside"):
            load_config(config_path=None, overrides=self._overrides(nested_import, dest_root))

    def test_import_dir_equal_to_dest_root_rejected(self, tmp_path: Path) -> None:
        import_dir, _ = _dirs(tmp_path)

        with pytest.raises(ConfigError, match="must not be the same directory"):
            load_config(config_path=None, overrides=self._overrides(import_dir, import_dir))

    def test_unresolvable_timezone_rejected(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        with pytest.raises(ConfigError, match="not a recognised IANA timezone"):
            load_config(
                config_path=None,
                overrides=self._overrides(import_dir, dest_root, timezone="Not/AZone"),
            )

    @pytest.mark.parametrize("field", ["tag_refresh_seconds", "stability_seconds", "batch_size"])
    def test_non_positive_interval_rejected(self, tmp_path: Path, field: str) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        with pytest.raises(ConfigError, match="positive integer"):
            load_config(
                config_path=None, overrides=self._overrides(import_dir, dest_root, **{field: 0})
            )

    def test_wrong_type_rejected(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)

        with pytest.raises(ConfigError, match="competition_id"):
            load_config(
                config_path=None,
                overrides=self._overrides(import_dir, dest_root, competition_id="not-an-int"),
            )


class TestExampleToml:
    """Guards against `teamgym-watcher.example.toml` drifting from `Config`'s fields."""

    def test_every_config_field_is_documented(self) -> None:
        text = EXAMPLE_TOML_PATH.read_text(encoding="utf-8")
        toml_keys = set(tomllib.loads(text))
        field_names = {f.name for f in fields(Config)}

        # `timezone` is optional and shown commented-out (its default is "use the machine's
        # own timezone"), so it won't appear as a live key -- it must still appear in the
        # file text so a future field isn't silently dropped from the example entirely.
        documented_but_commented = {"timezone"}
        assert toml_keys | documented_but_commented == field_names
        for name in documented_but_commented:
            assert f"{name} = " in text, f"'{name}' has no documented (commented) example line"

    def test_parses_and_loads_with_real_dirs(self, tmp_path: Path) -> None:
        import_dir, dest_root = _dirs(tmp_path)
        raw = tomllib.loads(EXAMPLE_TOML_PATH.read_text(encoding="utf-8"))
        raw["import_dir"] = import_dir.as_posix()
        raw["dest_root"] = dest_root.as_posix()

        config_path = tmp_path / "teamgym-watcher.toml"
        config_path.write_text(_to_toml(raw), encoding="utf-8")

        cfg = load_config(config_path=config_path)

        assert cfg.api_base_url == raw["api_base_url"]
        assert cfg.competition_id == raw["competition_id"]
