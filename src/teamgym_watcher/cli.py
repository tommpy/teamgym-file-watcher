"""Typer CLI entry point for teamgym-watcher."""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

from teamgym_watcher import __version__
from teamgym_watcher.api import ApiClient
from teamgym_watcher.auth import (
    AuthError,
    decode_jwt_payload,
    get_valid_token,
    run_device_flow,
    save_token_cache,
    token_cache_path,
)
from teamgym_watcher.auth import logout as revoke_refresh_token
from teamgym_watcher.config import Config, ConfigError, build_config, resolve_api_base_url
from teamgym_watcher.dashboard import (
    AUTH_REQUIRED_MESSAGE,
    CONNECTION_ERROR_MESSAGE,
    DashboardState,
    LiveDashboard,
    should_use_dashboard,
)
from teamgym_watcher.exif import ExifToolUnavailableError, ensure_exiftool_available
from teamgym_watcher.journal import Journal, Stats, StatsSnapshot
from teamgym_watcher.log import setup_logging
from teamgym_watcher.mover import group_files
from teamgym_watcher.pipeline import (
    BatchResult,
    CompetitionNameCache,
    PipelineDeps,
    format_summary_line,
    process_batch,
)
from teamgym_watcher.watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable

    from teamgym_watcher.mover import PhotoGroup


class UiMode(StrEnum):
    """``--ui`` choices: ``auto`` detects a real terminal, ``dashboard``/``plain`` force it."""

    AUTO = "auto"
    DASHBOARD = "dashboard"
    PLAIN = "plain"


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
_ApiBaseUrlOption = Annotated[
    str | None,
    typer.Option(
        "--api-base-url",
        help="Override API base URL directly; takes precedence over --local/--staging.",
    ),
]
_LocalOption = Annotated[
    bool,
    typer.Option("--local", help="Use local API base URL (http://localhost:3001/api)."),
]
_StagingOption = Annotated[
    bool,
    typer.Option(
        "--staging",
        help="Use staging API base URL (https://api-staging.tagging.teamgymphotos.com/api).",
    ),
]
_ImportDirOption = Annotated[
    Path, typer.Option("--import-dir", help="Directory to watch for new photos.")
]
_DestRootOption = Annotated[
    Path, typer.Option("--dest-root", help="Root directory to move sorted photos into.")
]
_TimezoneOption = Annotated[
    str | None,
    typer.Option(
        "--timezone",
        help="IANA timezone fallback for photos without an embedded UTC offset.",
    ),
]
_TimeOffsetOption = Annotated[
    int,
    typer.Option("--time-offset-seconds", help="Camera clock-skew correction, in seconds."),
]
_StabilityOption = Annotated[
    int,
    typer.Option(
        "--stability-seconds",
        help="Seconds a file's size must be unchanged before processing.",
    ),
]
_BatchSizeOption = Annotated[
    int, typer.Option("--batch-size", help="Files to drain per processing batch.")
]
_DryRunOption = Annotated[
    bool,
    typer.Option("--dry-run/--no-dry-run", help="Log actions without moving any files."),
]
_RecursiveOption = Annotated[
    bool,
    typer.Option("--recursive/--no-recursive", help="Watch import-dir recursively."),
]
_ExiftoolEnabledOption = Annotated[
    bool,
    typer.Option(
        "--exiftool-enabled/--no-exiftool-enabled",
        help="Enable exiftool fallback for RAW formats unsupported by exifread.",
    ),
]
_ExiftoolPathOption = Annotated[
    str,
    typer.Option("--exiftool-path", help="Path to the exiftool executable."),
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
_UiOption = Annotated[
    UiMode,
    typer.Option(
        "--ui",
        help=(
            "Terminal UI mode: auto (dashboard on a real terminal, plain otherwise), "
            "dashboard (force the live dashboard), or plain (force plain log output)."
        ),
    ),
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


def _resolve_config(
    *,
    verbose: bool,
    log_file: Path | None,
    api_base_url: str | None,
    local: bool,
    staging: bool,
    import_dir: Path,
    dest_root: Path,
    timezone: str | None,
    time_offset_seconds: int,
    stability_seconds: int,
    batch_size: int,
    dry_run: bool,
    exiftool_enabled: bool,
    exiftool_path: str,
    recursive: bool,
    console: Console | None = None,
) -> Config:
    setup_logging(verbose=verbose, log_file=log_file, console=console)
    try:
        resolved_api_base_url = resolve_api_base_url(
            local=local,
            staging=staging,
            api_base_url=api_base_url,
        )
        cfg = build_config(
            api_base_url=resolved_api_base_url,
            import_dir=import_dir,
            dest_root=dest_root,
            timezone=timezone,
            time_offset_seconds=time_offset_seconds,
            stability_seconds=stability_seconds,
            batch_size=batch_size,
            dry_run=dry_run,
            exiftool_enabled=exiftool_enabled,
            exiftool_path=exiftool_path,
            recursive=recursive,
        )
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from exc
    logger.info(
        "Loaded configuration (import_dir=%s, dest_root=%s, dry_run=%s).",
        cfg.import_dir,
        cfg.dest_root,
        cfg.dry_run,
    )
    return cfg


def _resolve_auth_api_base_url(
    *, verbose: bool, api_base_url: str | None, local: bool, staging: bool
) -> str:
    setup_logging(verbose=verbose, log_file=None)
    try:
        return resolve_api_base_url(local=local, staging=staging, api_base_url=api_base_url)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from exc


def _build_deps(cfg: Config, *, interactive: bool) -> PipelineDeps:
    """Auth check: everything ``run``/``process-once`` need before they can start draining
    photos. The first API request is deferred until the first batch's ``fetch_tags_window``.

    ``ApiClient`` is given a token-provider callback (re-calling ``get_valid_token``) rather
    than a fixed token string, so a JWT silently refreshed hours into a long-running ``run``
    is actually used by later requests instead of the client being stuck with the token it
    was constructed with -- see ``docs/deviations.md``.
    """
    try:
        ensure_exiftool_available(cfg)
    except ExifToolUnavailableError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=EXIFTOOL_ERROR_EXIT_CODE) from exc

    try:
        get_valid_token(cfg, interactive=interactive, cache_path=token_cache_path())
    except AuthError as exc:
        logger.error("Authentication failed: %s", exc)
        raise typer.Exit(code=AUTH_ERROR_EXIT_CODE) from exc

    def _token_provider() -> str:
        return get_valid_token(cfg, interactive=interactive, cache_path=token_cache_path())

    api_client = ApiClient(cfg.api_base_url, _token_provider)

    return PipelineDeps(
        config=cfg,
        api_client=api_client,
        journal=Journal(cfg.dest_root),
        stats=Stats(),
        competition_name=CompetitionNameCache(),
    )


def _log_auth_required(console: Console | None = None) -> None:
    logger.warning(AUTH_REQUIRED_MESSAGE)
    (console or Console()).print(
        Panel(
            AUTH_REQUIRED_MESSAGE, title="TeamGym API Authentication Required", style="bold yellow"
        )
    )


def _log_connection_error(message: str | None, console: Console | None = None) -> None:
    text = message or CONNECTION_ERROR_MESSAGE
    logger.warning(text)
    (console or Console()).print(Panel(text, title="TeamGym API Unreachable", style="bold red"))


def _build_dashboard_state(
    deps: PipelineDeps,
    watcher: Watcher,
    *,
    started_at: datetime,
    mode: str,
    auth_ok: bool,
    connection_ok: bool = True,
    connection_message: str | None = None,
) -> DashboardState:
    snapshot = deps.stats.snapshot()
    return DashboardState(
        started_at=started_at,
        now=datetime.now(UTC),
        mode=mode,
        dry_run=deps.config.dry_run,
        import_dir=deps.config.import_dir,
        dest_root=deps.config.dest_root,
        api_base_url=deps.config.api_base_url,
        processed=snapshot.processed,
        matched=snapshot.matched,
        random=snapshot.random,
        errors=snapshot.errors,
        queued=len(watcher.queue),
        auth_ok=auth_ok,
        auth_message=None if auth_ok else AUTH_REQUIRED_MESSAGE,
        connection_ok=connection_ok,
        connection_message=(
            None if connection_ok else (connection_message or CONNECTION_ERROR_MESSAGE)
        ),
    )


def _process_ready_groups(
    watcher: Watcher,
    groups: list[PhotoGroup],
    deps: PipelineDeps,
    *,
    on_chunk: Callable[[BatchResult], None] | None = None,
) -> tuple[list[PhotoGroup], bool, bool]:
    """Process ``groups`` in ``deps.config.batch_size``-sized chunks.

    PLAN.md's "Batching / API economy" requires photos to be drained in batches (default 25)
    so log output and journal writes are chunked even when a rescan or long-idle period hands
    back far more than one chunk's worth of ready files at once. Logs the summary line on
    every chunk's completion.

    ``on_chunk``, when given, is called after every chunk with that chunk's
    :class:`~teamgym_watcher.pipeline.BatchResult` -- the caller (``_run_watch_loop``/
    ``_drain_once``) uses this to refresh a live dashboard without this function needing to
    know anything about dashboards itself.

    Returns the groups left unattempted because authentication was required, or the API was
    unreachable, partway through a chunk, and which of those two happened -- the caller must
    keep the groups queued and retry, never drop them.
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
        if on_chunk is not None:
            on_chunk(result)
        if result.auth_paused or result.connection_error:
            return (
                [*result.pending, *groups[start + len(chunk) :]],
                result.auth_paused,
                result.connection_error,
            )
    return [], False, False


def _run_watch_loop(
    watcher: Watcher,
    deps: PipelineDeps,
    *,
    stop_event: threading.Event,
    poll_interval_seconds: float = 1.0,
    summary_interval_seconds: float = 60.0,
    auth_log_interval_seconds: float = 60.0,
    connection_log_interval_seconds: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    dashboard: LiveDashboard | None = None,
    console: Console | None = None,
) -> StatsSnapshot:
    """Drain ready batches forever until ``stop_event`` is set (graceful Ctrl-C: the current
    batch always finishes before the loop checks the event again).

    An unreachable API (connection refused, timeout) never crashes this loop: ``process_batch``
    turns it into a paused batch exactly like an expired token, so the ready groups stay queued
    and are retried on the next poll tick once the server comes back.
    """
    pending: list[PhotoGroup] = []
    last_summary = clock()
    last_auth_log = 0.0
    last_connection_log = 0.0
    started_at = datetime.now(UTC)
    auth_ok = True
    connection_ok = True
    connection_message: str | None = None

    def _refresh_dashboard() -> None:
        if dashboard is not None:
            dashboard.update(
                _build_dashboard_state(
                    deps,
                    watcher,
                    started_at=started_at,
                    mode="run",
                    auth_ok=auth_ok,
                    connection_ok=connection_ok,
                    connection_message=connection_message,
                )
            )

    def _on_chunk(result: BatchResult) -> None:
        nonlocal auth_ok, connection_ok, connection_message
        auth_ok = not result.auth_paused
        connection_ok = not result.connection_error
        connection_message = result.connection_message if result.connection_error else None
        _refresh_dashboard()

    _refresh_dashboard()

    while not stop_event.is_set():
        ready = watcher.poll_ready()
        groups = [*pending, *group_files(ready)]
        pending = []

        if groups:
            pending, auth_paused, connection_error = _process_ready_groups(
                watcher, groups, deps, on_chunk=_on_chunk
            )
            auth_ok = not auth_paused
            connection_ok = not connection_error
            if auth_paused:
                now = clock()
                if now - last_auth_log >= auth_log_interval_seconds:
                    _log_auth_required(console)
                    last_auth_log = now
            if connection_error:
                now = clock()
                if now - last_connection_log >= connection_log_interval_seconds:
                    _log_connection_error(connection_message, console)
                    last_connection_log = now

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

        _refresh_dashboard()

        if not groups:
            sleep(poll_interval_seconds)

    final = deps.stats.snapshot()
    logger.info(
        "Stopping: %s",
        format_summary_line(
            final.processed, final.matched, final.random, final.errors, len(watcher.queue)
        ),
    )
    _refresh_dashboard()
    return final


def _drain_once(
    watcher: Watcher,
    deps: PipelineDeps,
    *,
    sleep: Callable[[float], None] = time.sleep,
    dashboard: LiveDashboard | None = None,
) -> tuple[bool, bool]:
    """Fully drain whatever is currently in the import directory.

    Returns ``(auth_paused, connection_error)``. Either one ends the drain early -- any
    not-yet-processed files are left untouched in the import directory (never dropped) and
    will be retried on the next run. ``process-once`` only drains once, so an unreachable API
    is reported back to the caller to exit non-zero rather than retried in a loop here (unlike
    ``_run_watch_loop``, which retries indefinitely).
    """
    watcher.rescan(deps.journal.load_processed())
    pending: list[PhotoGroup] = []
    started_at = datetime.now(UTC)
    auth_ok = True
    connection_ok = True
    connection_message: str | None = None

    def _refresh_dashboard() -> None:
        if dashboard is not None:
            dashboard.update(
                _build_dashboard_state(
                    deps,
                    watcher,
                    started_at=started_at,
                    mode="process-once",
                    auth_ok=auth_ok,
                    connection_ok=connection_ok,
                    connection_message=connection_message,
                )
            )

    def _on_chunk(result: BatchResult) -> None:
        nonlocal auth_ok, connection_ok, connection_message
        auth_ok = not result.auth_paused
        connection_ok = not result.connection_error
        connection_message = result.connection_message if result.connection_error else None
        _refresh_dashboard()

    _refresh_dashboard()

    while True:
        ready = watcher.poll_ready()
        groups = [*pending, *group_files(ready)]
        pending = []

        if not groups:
            if len(watcher.queue) == 0:
                _refresh_dashboard()
                return False, False
            sleep(deps.config.stability_seconds)
            continue

        pending, auth_paused, connection_error = _process_ready_groups(
            watcher, groups, deps, on_chunk=_on_chunk
        )
        if auth_paused or connection_error:
            return auth_paused, connection_error


@app.command()
def run(
    import_dir: _ImportDirOption,
    dest_root: _DestRootOption,
    verbose: _VerboseOption = False,
    api_base_url: _ApiBaseUrlOption = None,
    local: _LocalOption = False,
    staging: _StagingOption = False,
    timezone: _TimezoneOption = None,
    time_offset_seconds: _TimeOffsetOption = 0,
    stability_seconds: _StabilityOption = 3,
    batch_size: _BatchSizeOption = 25,
    dry_run: _DryRunOption = False,
    recursive: _RecursiveOption = False,
    exiftool_enabled: _ExiftoolEnabledOption = False,
    exiftool_path: _ExiftoolPathOption = "exiftool",
    non_interactive: _NonInteractiveOption = False,
    log_file: _LogFileOption = None,
    ui: _UiOption = UiMode.AUTO,
) -> None:
    """Watch the import directory forever, matching and moving photos as they arrive."""
    console = Console()
    cfg = _resolve_config(
        verbose=verbose,
        log_file=log_file,
        api_base_url=api_base_url,
        local=local,
        staging=staging,
        import_dir=import_dir,
        dest_root=dest_root,
        timezone=timezone,
        time_offset_seconds=time_offset_seconds,
        stability_seconds=stability_seconds,
        batch_size=batch_size,
        dry_run=dry_run,
        recursive=recursive,
        exiftool_enabled=exiftool_enabled,
        exiftool_path=exiftool_path,
        console=console,
    )
    deps = _build_deps(cfg, interactive=not non_interactive)
    watcher = Watcher(
        cfg.import_dir, recursive=cfg.recursive, stability_seconds=cfg.stability_seconds
    )
    watcher.rescan(deps.journal.load_processed())
    watcher.start(already_processed=deps.journal.load_processed)

    dashboard = LiveDashboard(console) if should_use_dashboard(console, ui.value) else None

    stop_event = threading.Event()

    def _handle_sigint(signum: int, frame: object) -> None:
        logger.warning("Interrupted; finishing the current batch and shutting down...")
        stop_event.set()

    previous_handler = signal.signal(signal.SIGINT, _handle_sigint)
    if dashboard is not None:
        dashboard.start()
    try:
        _run_watch_loop(watcher, deps, stop_event=stop_event, dashboard=dashboard, console=console)
    finally:
        if dashboard is not None:
            dashboard.stop()
        signal.signal(signal.SIGINT, previous_handler)
        watcher.stop()


@app.command("process-once")
def process_once(
    import_dir: _ImportDirOption,
    dest_root: _DestRootOption,
    verbose: _VerboseOption = False,
    api_base_url: _ApiBaseUrlOption = None,
    local: _LocalOption = False,
    staging: _StagingOption = False,
    timezone: _TimezoneOption = None,
    time_offset_seconds: _TimeOffsetOption = 0,
    stability_seconds: _StabilityOption = 3,
    batch_size: _BatchSizeOption = 25,
    dry_run: _DryRunOption = False,
    recursive: _RecursiveOption = False,
    exiftool_enabled: _ExiftoolEnabledOption = False,
    exiftool_path: _ExiftoolPathOption = "exiftool",
    non_interactive: _NonInteractiveOption = False,
    log_file: _LogFileOption = None,
    ui: _UiOption = UiMode.AUTO,
) -> None:
    """Drain the import directory once and exit."""
    console = Console()
    cfg = _resolve_config(
        verbose=verbose,
        log_file=log_file,
        api_base_url=api_base_url,
        local=local,
        staging=staging,
        import_dir=import_dir,
        dest_root=dest_root,
        timezone=timezone,
        time_offset_seconds=time_offset_seconds,
        stability_seconds=stability_seconds,
        batch_size=batch_size,
        dry_run=dry_run,
        recursive=recursive,
        exiftool_enabled=exiftool_enabled,
        exiftool_path=exiftool_path,
        console=console,
    )
    deps = _build_deps(cfg, interactive=not non_interactive)
    watcher = Watcher(
        cfg.import_dir, recursive=cfg.recursive, stability_seconds=cfg.stability_seconds
    )

    dashboard = LiveDashboard(console) if should_use_dashboard(console, ui.value) else None
    if dashboard is not None:
        dashboard.start()
    try:
        auth_paused, connection_error = _drain_once(watcher, deps, dashboard=dashboard)
    finally:
        if dashboard is not None:
            dashboard.stop()
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

    if connection_error:
        logger.error("Could not reach the TeamGym API; check your connection and try again.")
        raise typer.Exit(code=API_ERROR_EXIT_CODE)
    if auth_paused:
        logger.error("Authentication required; run `teamgym-watcher auth` and try again.")
        raise typer.Exit(code=AUTH_ERROR_EXIT_CODE)
    if snapshot.errors > 0:
        raise typer.Exit(code=PROCESSING_ERROR_EXIT_CODE)


@app.command()
def auth(
    verbose: _VerboseOption = False,
    api_base_url: _ApiBaseUrlOption = None,
    local: _LocalOption = False,
    staging: _StagingOption = False,
) -> None:
    """Force a fresh GitHub device-flow login, cache the JWT, and print the identity."""
    resolved_api_base_url = _resolve_auth_api_base_url(
        verbose=verbose, api_base_url=api_base_url, local=local, staging=staging
    )

    with httpx.Client(timeout=30.0) as client:
        try:
            token = run_device_flow(client, resolved_api_base_url)
            claims = decode_jwt_payload(token.access_token)
        except AuthError as exc:
            logger.error("Authentication failed: %s", exc)
            raise typer.Exit(code=AUTH_ERROR_EXIT_CODE) from exc

    save_token_cache(token_cache_path(), token)
    logger.info(
        "Authenticated as %s <%s>.", claims.get("login", "unknown"), claims.get("email", "unknown")
    )


@app.command()
def logout(
    verbose: _VerboseOption = False,
    api_base_url: _ApiBaseUrlOption = None,
    local: _LocalOption = False,
    staging: _StagingOption = False,
) -> None:
    """Revoke the cached refresh token (best-effort) and clear the local token cache."""
    resolved_api_base_url = _resolve_auth_api_base_url(
        verbose=verbose, api_base_url=api_base_url, local=local, staging=staging
    )
    revoke_refresh_token(resolved_api_base_url, token_cache_path())
    logger.info("Logged out; local token cache cleared.")


if __name__ == "__main__":
    app()
