"""Typed configuration validation and environment URL resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCAL_API_BASE_URL: Final = "http://localhost:3001/api"
STAGING_API_BASE_URL: Final = "https://api-staging.tagging.teamgymphotos.com/api"
PROD_API_BASE_URL: Final = "https://api.tagging.teamgymphotos.com/api"


class ConfigError(Exception):
    """Raised for an invalid configuration value.

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
    exiftool_enabled: bool = False
    exiftool_path: str = "exiftool"
    recursive: bool = False


def resolve_api_base_url(*, local: bool, staging: bool, api_base_url: str | None) -> str:
    if api_base_url is not None:
        return api_base_url
    if local and staging:
        raise ConfigError("--local and --staging cannot both be set.")
    if local:
        return LOCAL_API_BASE_URL
    if staging:
        return STAGING_API_BASE_URL
    return PROD_API_BASE_URL


def build_config(
    *,
    api_base_url: str,
    competition_id: int,
    import_dir: Path,
    dest_root: Path,
    timezone: str | None = None,
    time_offset_seconds: int = 0,
    tag_refresh_seconds: int = 60,
    stability_seconds: int = 3,
    batch_size: int = 25,
    dry_run: bool = False,
    exiftool_enabled: bool = False,
    exiftool_path: str = "exiftool",
    recursive: bool = False,
) -> Config:
    _validate_positive(tag_refresh_seconds, "tag_refresh_seconds")
    _validate_positive(stability_seconds, "stability_seconds")
    _validate_positive(batch_size, "batch_size")
    _validate_import_dir(import_dir)
    _validate_not_nested(import_dir, dest_root)
    _validate_dest_root(dest_root)
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
        exiftool_enabled=exiftool_enabled,
        exiftool_path=exiftool_path,
        recursive=recursive,
    )


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
