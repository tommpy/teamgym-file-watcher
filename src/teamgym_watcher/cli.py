"""Typer CLI entry point for teamgym-watcher."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import httpx
import typer

from teamgym_watcher import __version__
from teamgym_watcher.auth import (
    AuthError,
    decode_jwt_payload,
    run_device_flow,
    save_token_cache,
    token_cache_path,
)
from teamgym_watcher.config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from teamgym_watcher.log import setup_logging

app = typer.Typer(
    name="teamgym-watcher",
    help=(
        "Watches an import directory, matches photos to TeamGym competition tags by "
        "capture time, and moves them into a Zenfolio-ready folder hierarchy."
    ),
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)

NOT_IMPLEMENTED_EXIT_CODE = 1
CONFIG_ERROR_EXIT_CODE = 2
AUTH_ERROR_EXIT_CODE = 3

_VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging.")]
_ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", help=f"Path to the TOML config file (default: {DEFAULT_CONFIG_FILENAME})."
    ),
]
_ApiBaseUrlOption = Annotated[
    str | None, typer.Option("--api-base-url", help="Base URL of the teamgym-sorter API.")
]
_CompetitionIdOption = Annotated[
    int | None, typer.Option("--competition-id", help="Competition id to fetch tags for.")
]
_ImportDirOption = Annotated[
    Path | None, typer.Option("--import-dir", help="Directory to watch for new photos.")
]
_DestRootOption = Annotated[
    Path | None, typer.Option("--dest-root", help="Root directory to move sorted photos into.")
]
_TimezoneOption = Annotated[
    str | None,
    typer.Option(
        "--timezone", help="IANA timezone fallback for photos without an embedded UTC offset."
    ),
]
_TimeOffsetOption = Annotated[
    int | None,
    typer.Option("--time-offset-seconds", help="Camera clock-skew correction, in seconds."),
]
_TagRefreshOption = Annotated[
    int | None, typer.Option("--tag-refresh-seconds", help="How often to refresh the tag cache.")
]
_StabilityOption = Annotated[
    int | None,
    typer.Option(
        "--stability-seconds", help="Seconds a file's size must be unchanged before processing."
    ),
]
_BatchSizeOption = Annotated[
    int | None, typer.Option("--batch-size", help="Files to drain per processing batch.")
]
_DryRunOption = Annotated[
    bool | None,
    typer.Option("--dry-run/--no-dry-run", help="Log actions without moving any files."),
]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"teamgym-watcher {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """teamgym-watcher: sort TeamGym competition photos into a Zenfolio-ready folder hierarchy."""


def _build_overrides(
    *,
    api_base_url: str | None,
    competition_id: int | None,
    import_dir: Path | None,
    dest_root: Path | None,
    timezone: str | None,
    time_offset_seconds: int | None,
    tag_refresh_seconds: int | None,
    stability_seconds: int | None,
    batch_size: int | None,
    dry_run: bool | None,
) -> dict[str, object]:
    values: dict[str, object | None] = {
        "api_base_url": api_base_url,
        "competition_id": competition_id,
        "import_dir": import_dir,
        "dest_root": dest_root,
        "timezone": timezone,
        "time_offset_seconds": time_offset_seconds,
        "tag_refresh_seconds": tag_refresh_seconds,
        "stability_seconds": stability_seconds,
        "batch_size": batch_size,
        "dry_run": dry_run,
    }
    return {key: value for key, value in values.items() if value is not None}


def _resolve_config(
    verbose: bool, config_path: Path | None, overrides: dict[str, object]
) -> Config:
    setup_logging(verbose=verbose)
    try:
        cfg = load_config(config_path=config_path, overrides=overrides)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from exc
    logger.info(
        "Loaded configuration for competition %s (import_dir=%s, dest_root=%s, dry_run=%s).",
        cfg.competition_id,
        cfg.import_dir,
        cfg.dest_root,
        cfg.dry_run,
    )
    return cfg


def _not_implemented(command: str) -> None:
    logger.error("'%s' is not implemented yet.", command)
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


@app.command()
def run(
    verbose: _VerboseOption = False,
    config: _ConfigOption = None,
    api_base_url: _ApiBaseUrlOption = None,
    competition_id: _CompetitionIdOption = None,
    import_dir: _ImportDirOption = None,
    dest_root: _DestRootOption = None,
    timezone: _TimezoneOption = None,
    time_offset_seconds: _TimeOffsetOption = None,
    tag_refresh_seconds: _TagRefreshOption = None,
    stability_seconds: _StabilityOption = None,
    batch_size: _BatchSizeOption = None,
    dry_run: _DryRunOption = None,
) -> None:
    """Watch the import directory forever, matching and moving photos as they arrive."""
    overrides = _build_overrides(
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
    _resolve_config(verbose, config, overrides)
    _not_implemented("run")


@app.command("process-once")
def process_once(
    verbose: _VerboseOption = False,
    config: _ConfigOption = None,
    api_base_url: _ApiBaseUrlOption = None,
    competition_id: _CompetitionIdOption = None,
    import_dir: _ImportDirOption = None,
    dest_root: _DestRootOption = None,
    timezone: _TimezoneOption = None,
    time_offset_seconds: _TimeOffsetOption = None,
    tag_refresh_seconds: _TagRefreshOption = None,
    stability_seconds: _StabilityOption = None,
    batch_size: _BatchSizeOption = None,
    dry_run: _DryRunOption = None,
) -> None:
    """Drain the import directory once and exit."""
    overrides = _build_overrides(
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
    _resolve_config(verbose, config, overrides)
    _not_implemented("process-once")


@app.command()
def auth(
    verbose: _VerboseOption = False,
    config: _ConfigOption = None,
    api_base_url: _ApiBaseUrlOption = None,
    competition_id: _CompetitionIdOption = None,
    import_dir: _ImportDirOption = None,
    dest_root: _DestRootOption = None,
    timezone: _TimezoneOption = None,
    time_offset_seconds: _TimeOffsetOption = None,
    tag_refresh_seconds: _TagRefreshOption = None,
    stability_seconds: _StabilityOption = None,
    batch_size: _BatchSizeOption = None,
    dry_run: _DryRunOption = None,
) -> None:
    """Force a fresh GitHub device-flow login, cache the JWT, and print the identity."""
    overrides = _build_overrides(
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
    cfg = _resolve_config(verbose, config, overrides)

    with httpx.Client(timeout=30.0) as client:
        try:
            token = run_device_flow(client, cfg.api_base_url)
            claims = decode_jwt_payload(token.access_token)
        except AuthError as exc:
            logger.error("Authentication failed: %s", exc)
            raise typer.Exit(code=AUTH_ERROR_EXIT_CODE) from exc

    save_token_cache(token_cache_path(), token)
    logger.info(
        "Authenticated as %s <%s>.", claims.get("login", "unknown"), claims.get("email", "unknown")
    )


if __name__ == "__main__":
    app()
