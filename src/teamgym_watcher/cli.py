"""Typer CLI entry point for teamgym-watcher."""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

from teamgym_watcher import __version__
from teamgym_watcher.api import ApiClient, ApiError
from teamgym_watcher.auth import (
    AuthError,
    decode_jwt_payload,
    get_valid_token,
    run_device_flow,
    save_token_cache,
    token_cache_path,
)
from teamgym_watcher.config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from teamgym_watcher.exif import ExifToolUnavailableError, ensure_exiftool_available
from teamgym_watcher.journal import Journal, Stats, StatsSnapshot
from teamgym_watcher.log import setup_logging
from teamgym_watcher.mover import group_files
from teamgym_watcher.pipeline import PipelineDeps, format_summary_line, process_batch
from teamgym_watcher.tag_cache import TagCache
from teamgym_watcher.watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable

    from teamgym_watcher.mover import PhotoGroup

app = typer.Typer(
    name="teamgym-watcher",
    help=(
        "Watches an import directory, matches photos to TeamGym competition tags by "
        "capture time, and moves them into a Zenfolio-ready folder hierarchy."
    ),
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)

CONFIG_ERROR_EXIT_CODE = 2
AUTH_ERROR_EXIT_CODE = 3
EXIFTOOL_ERROR_EXIT_CODE = 4
API_ERROR_EXIT_CODE = 5
PROCESSING_ERROR_EXIT_CODE = 6

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
_NonInteractiveOption = Annotated[
    bool,
    typer.Option(
        "--non-interactive",
        help="Never prompt for interactive re-authentication; pause and wait instead.",
    ),
]
_LogFileOption = Annotated[
    Path | None, typer.Option("--log-file", help="Also write logs to this rotating file.")
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
    verbose: bool,
    config_path: Path | None,
    overrides: dict[str, object],
    log_file: Path | None = None,
) -> Config:
    setup_logging(verbose=verbose, log_file=log_file)
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


def _build_deps(cfg: Config, *, interactive: bool) -> PipelineDeps:
    """Auth check + initial full tag fetch: everything ``run``/``process-once`` need before
    they can start draining photos."""
    try:
        ensure_exiftool_available(cfg)
    except ExifToolUnavailableError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=EXIFTOOL_ERROR_EXIT_CODE) from exc

    try:
        token = get_valid_token(cfg, interactive=interactive, cache_path=token_cache_path())
    except AuthError as exc:
        logger.error("Authentication failed: %s", exc)
        raise typer.Exit(code=AUTH_ERROR_EXIT_CODE) from exc

    api_client = ApiClient(cfg.api_base_url, token)
    tag_cache = TagCache(cfg.competition_id, tag_refresh_seconds=cfg.tag_refresh_seconds)
    try:
        tag_cache.refresh(api_client)
    except ApiError as exc:
        logger.error("Initial tag fetch failed: %s", exc)
        raise typer.Exit(code=API_ERROR_EXIT_CODE) from exc

    return PipelineDeps(
        config=cfg,
        tag_cache=tag_cache,
        api_client=api_client,
        journal=Journal(cfg.dest_root),
        stats=Stats(),
    )


def _log_auth_required() -> None:
    message = "Authentication required: run `teamgym-watcher auth` to re-authenticate."
    logger.warning(message)
    Console().print(
        Panel(message, title="TeamGym API Authentication Required", style="bold yellow")
    )


def _process_ready_groups(
    watcher: Watcher, groups: list[PhotoGroup], deps: PipelineDeps
) -> tuple[list[PhotoGroup], bool]:
    """Process ``groups`` in ``deps.config.batch_size``-sized chunks.

    PLAN.md's "Batching / API economy" requires photos to be drained in batches (default 25)
    so log output and journal writes are chunked even when a rescan or long-idle period hands
    back far more than one chunk's worth of ready files at once. Logs the summary line on
    every chunk's completion.

    Returns the groups left unattempted because authentication was required partway through a
    chunk, and whether that happened -- the caller must keep them queued and retry, never drop
    them.
    """
    size = deps.config.batch_size
    for start in range(0, len(groups), size):
        chunk = groups[start : start + size]
        result = process_batch(chunk, deps)
        snapshot = deps.stats.snapshot()
        logger.info(
            format_summary_line(
                snapshot.processed,
                snapshot.matched,
                snapshot.random,
                snapshot.errors,
                len(watcher.queue),
            )
        )
        if result.auth_paused:
            return [*result.pending, *groups[start + len(chunk) :]], True
    return [], False


def _run_watch_loop(
    watcher: Watcher,
    deps: PipelineDeps,
    *,
    stop_event: threading.Event,
    poll_interval_seconds: float = 1.0,
    summary_interval_seconds: float = 60.0,
    auth_log_interval_seconds: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> StatsSnapshot:
    """Drain ready batches forever until ``stop_event`` is set (graceful Ctrl-C: the current
    batch always finishes before the loop checks the event again)."""
    pending: list[PhotoGroup] = []
    last_summary = clock()
    last_auth_log = 0.0

    while not stop_event.is_set():
        ready = watcher.poll_ready()
        groups = [*pending, *group_files(ready)]
        pending = []

        if groups:
            pending, auth_paused = _process_ready_groups(watcher, groups, deps)
            if auth_paused:
                now = clock()
                if now - last_auth_log >= auth_log_interval_seconds:
                    _log_auth_required()
                    last_auth_log = now

        now = clock()
        if now - last_summary >= summary_interval_seconds:
            snapshot = deps.stats.snapshot()
            logger.info(
                format_summary_line(
                    snapshot.processed,
                    snapshot.matched,
                    snapshot.random,
                    snapshot.errors,
                    len(watcher.queue),
                )
            )
            last_summary = now

        if not groups:
            sleep(poll_interval_seconds)

    final = deps.stats.snapshot()
    logger.info(
        "Stopping: %s",
        format_summary_line(
            final.processed, final.matched, final.random, final.errors, len(watcher.queue)
        ),
    )
    return final


def _drain_once(
    watcher: Watcher, deps: PipelineDeps, *, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Fully drain whatever is currently in the import directory.

    Returns ``True`` if authentication was required mid-drain -- any not-yet-processed files
    are left untouched in the import directory (never dropped) and will be retried on the
    next run.
    """
    watcher.rescan(deps.journal.load_processed())
    pending: list[PhotoGroup] = []

    while True:
        ready = watcher.poll_ready()
        groups = [*pending, *group_files(ready)]
        pending = []

        if not groups:
            if len(watcher.queue) == 0:
                return False
            sleep(deps.config.stability_seconds)
            continue

        pending, auth_paused = _process_ready_groups(watcher, groups, deps)
        if auth_paused:
            return True


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
    non_interactive: _NonInteractiveOption = False,
    log_file: _LogFileOption = None,
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
    cfg = _resolve_config(verbose, config, overrides, log_file)
    deps = _build_deps(cfg, interactive=not non_interactive)
    watcher = Watcher(
        cfg.import_dir, recursive=cfg.recursive, stability_seconds=cfg.stability_seconds
    )
    watcher.rescan(deps.journal.load_processed())
    watcher.start(already_processed=deps.journal.load_processed)

    stop_event = threading.Event()

    def _handle_sigint(signum: int, frame: object) -> None:
        logger.warning("Interrupted; finishing the current batch and shutting down...")
        stop_event.set()

    previous_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        _run_watch_loop(watcher, deps, stop_event=stop_event)
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        watcher.stop()


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
    non_interactive: _NonInteractiveOption = False,
    log_file: _LogFileOption = None,
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
    cfg = _resolve_config(verbose, config, overrides, log_file)
    deps = _build_deps(cfg, interactive=not non_interactive)
    watcher = Watcher(
        cfg.import_dir, recursive=cfg.recursive, stability_seconds=cfg.stability_seconds
    )

    auth_paused = _drain_once(watcher, deps)
    snapshot = deps.stats.snapshot()
    logger.info(
        format_summary_line(
            snapshot.processed,
            snapshot.matched,
            snapshot.random,
            snapshot.errors,
            len(watcher.queue),
        )
    )

    if auth_paused:
        logger.error("Authentication required; run `teamgym-watcher auth` and try again.")
        raise typer.Exit(code=AUTH_ERROR_EXIT_CODE)
    if snapshot.errors > 0:
        raise typer.Exit(code=PROCESSING_ERROR_EXIT_CODE)


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
