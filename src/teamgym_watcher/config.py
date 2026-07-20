"""Typed TOML configuration with CLI-option overrides and validation.

Load order is TOML file → CLI overrides: :func:`load_config` reads the (optional) TOML
file first, then applies any non-``None`` values from ``overrides`` on top, and finally
validates the merged result into a frozen :class:`Config`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_CONFIG_FILENAME: Final = "teamgym-watcher.toml"


class ConfigError(Exception):
    """Raised for a missing, malformed, or invalid configuration value.

    Messages are written to be shown directly to the user (e.g. via the CLI), so they
    name the offending setting and how to fix it.
    """


@dataclass(frozen=True, slots=True)
class Config:
    """Fully resolved, validated runtime configuration."""

    api_base_url: str
    competition_id: int
    import_dir: Path
    dest_root: Path
    timezone: str | None = None
    time_offset_seconds: int = 0
    tag_refresh_seconds: int = 60
    stability_seconds: int = 3
    batch_size: int = 25
    dry_run: bool = False


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, object] | None = None,
) -> Config:
    """Load and validate configuration from a TOML file plus CLI overrides.

    ``config_path`` is the explicit ``--config`` value, if any. When omitted, the
    default ``teamgym-watcher.toml`` in the current working directory is used if it
    exists, and silently skipped otherwise (an explicitly named file must exist).
    ``overrides`` values that are ``None`` are treated as "not provided" and ignored,
    so CLI options default to ``None`` and only override the file when set.
    """
    raw: dict[str, object]
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"Config file not found: {config_path}")
        raw = _read_toml(config_path)
    else:
        default_path = Path(DEFAULT_CONFIG_FILENAME)
        raw = _read_toml(default_path) if default_path.is_file() else {}

    merged: dict[str, object] = dict(raw)
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})

    return _build_config(merged)


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file '{path}' is not valid TOML: {exc}") from exc


def _build_config(data: dict[str, object]) -> Config:
    api_base_url = _as_str(_require(data, "api_base_url", "--api-base-url"), "api_base_url")
    competition_id = _as_int(_require(data, "competition_id", "--competition-id"), "competition_id")
    import_dir = _as_path(_require(data, "import_dir", "--import-dir"), "import_dir")
    dest_root = _as_path(_require(data, "dest_root", "--dest-root"), "dest_root")

    timezone_raw = data.get("timezone")
    timezone = _as_str(timezone_raw, "timezone") if timezone_raw is not None else None

    time_offset_seconds = _as_int(data.get("time_offset_seconds", 0), "time_offset_seconds")
    tag_refresh_seconds = _as_int(data.get("tag_refresh_seconds", 60), "tag_refresh_seconds")
    stability_seconds = _as_int(data.get("stability_seconds", 3), "stability_seconds")
    batch_size = _as_int(data.get("batch_size", 25), "batch_size")
    dry_run = _as_bool(data.get("dry_run", False), "dry_run")

    _validate_positive(tag_refresh_seconds, "tag_refresh_seconds")
    _validate_positive(stability_seconds, "stability_seconds")
    _validate_positive(batch_size, "batch_size")
    _validate_import_dir(import_dir)
    _validate_dest_root(dest_root)
    _validate_not_nested(import_dir, dest_root)
    _validate_timezone(timezone)

    return Config(
        api_base_url=api_base_url,
        competition_id=competition_id,
        import_dir=import_dir,
        dest_root=dest_root,
        timezone=timezone,
        time_offset_seconds=time_offset_seconds,
        tag_refresh_seconds=tag_refresh_seconds,
        stability_seconds=stability_seconds,
        batch_size=batch_size,
        dry_run=dry_run,
    )


def _require(data: dict[str, object], key: str, cli_flag: str) -> object:
    if data.get(key) is None:
        raise ConfigError(
            f"Missing required setting '{key}'. Set it in the config TOML file or pass {cli_flag}."
        )
    return data[key]


def _as_str(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a non-empty string, got {value!r}.")
    return value


def _as_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{key}' must be an integer, got {value!r}.")
    return value


def _as_bool(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"'{key}' must be a boolean, got {value!r}.")
    return value


def _as_path(value: object, key: str) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' must be a non-empty path string, got {value!r}.")
    return Path(value)


def _validate_positive(value: int, key: str) -> None:
    if value <= 0:
        raise ConfigError(f"'{key}' must be a positive integer, got {value}.")


def _validate_import_dir(import_dir: Path) -> None:
    if not import_dir.exists():
        raise ConfigError(f"import_dir '{import_dir}' does not exist.")
    if not import_dir.is_dir():
        raise ConfigError(f"import_dir '{import_dir}' is not a directory.")


def _validate_dest_root(dest_root: Path) -> None:
    if dest_root.exists() and not dest_root.is_dir():
        raise ConfigError(f"dest_root '{dest_root}' exists and is not a directory.")
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"dest_root '{dest_root}' could not be created: {exc}") from exc


def _validate_not_nested(import_dir: Path, dest_root: Path) -> None:
    import_resolved = import_dir.resolve()
    dest_resolved = dest_root.resolve()
    if import_resolved == dest_resolved:
        raise ConfigError("import_dir and dest_root must not be the same directory.")
    if dest_resolved.is_relative_to(import_resolved):
        raise ConfigError(f"dest_root '{dest_root}' must not be inside import_dir '{import_dir}'.")
    if import_resolved.is_relative_to(dest_resolved):
        raise ConfigError(f"import_dir '{import_dir}' must not be inside dest_root '{dest_root}'.")


def _validate_timezone(timezone: str | None) -> None:
    if timezone is None:
        return
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"timezone '{timezone}' is not a recognised IANA timezone name.") from exc
