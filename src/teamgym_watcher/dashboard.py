"""Live terminal status dashboard: a pure renderable function plus a thin ``rich.live.Live``
wrapper, per prompts/17.

``render_dashboard`` is deliberately pure (state in, renderable out) so it can be tested by
rendering to a ``Console(record=True)``/``io.StringIO()`` without a real terminal.
``LiveDashboard`` is the only stateful piece, and it is always constructed with an explicit,
caller-supplied ``Console`` -- the whole point of this module is to share one ``Console``
with the existing ``RichHandler`` (see ``log.py``) rather than open a second one that would
corrupt the terminal while a ``Live`` region owns it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from teamgym_watcher.pipeline import format_summary_line

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from rich.console import Console, RenderableType

    from teamgym_watcher.journal import JournalStatus

AUTH_REQUIRED_MESSAGE: Final = (
    "Authentication required: run `teamgym-watcher auth` to re-authenticate."
)
CONNECTION_ERROR_MESSAGE: Final = "Cannot reach the TeamGym API; retrying automatically."


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One recent per-photo outcome. Reserved for a future recent-activity row in the
    dashboard layout; not required to be wired up anywhere yet."""

    timestamp: datetime
    filename: str
    status: JournalStatus
    detail: str


@dataclass(frozen=True, slots=True)
class DashboardState:
    """Everything :func:`render_dashboard` needs to draw one frame, built fresh by the
    caller on every tick/chunk -- this type carries no behaviour of its own."""

    started_at: datetime
    now: datetime
    mode: str
    dry_run: bool
    import_dir: Path
    dest_root: Path
    api_base_url: str
    processed: int
    matched: int
    random: int
    errors: int
    queued: int
    auth_ok: bool
    auth_message: str | None
    connection_ok: bool = True
    connection_message: str | None = None


def render_dashboard(state: DashboardState) -> RenderableType:
    """Build the dashboard frame for ``state``. Pure function: no I/O, no clock reads --
    everything comes from ``state``."""
    elapsed = timedelta(seconds=int((state.now - state.started_at).total_seconds()))
    dry_run_badge = " [bold yellow]DRY RUN[/bold yellow]" if state.dry_run else ""
    header = f"mode=[bold]{state.mode}[/bold]{dry_run_badge}  elapsed=[bold]{elapsed}[/bold]"

    paths = Table.grid(padding=(0, 1))
    paths.add_column()
    paths.add_column()
    paths.add_row("import_dir:", str(state.import_dir))
    paths.add_row("dest_root:", str(state.dest_root))
    paths.add_row("api:", state.api_base_url)

    stats_line = format_summary_line(
        state.processed, state.matched, state.random, state.errors, state.queued
    )

    if state.connection_ok:
        connection_line = "[bold green]Connected[/bold green]"
    else:
        connection_line = (
            f"[bold red]{state.connection_message or CONNECTION_ERROR_MESSAGE}[/bold red]"
        )

    if state.auth_ok:
        auth_line = "[bold green]Authenticated[/bold green]"
    else:
        auth_line = f"[bold yellow]{state.auth_message or AUTH_REQUIRED_MESSAGE}[/bold yellow]"

    return Panel(
        Group(header, paths, stats_line, connection_line, auth_line),
        title="teamgym-watcher",
        border_style="cyan",
    )


def should_use_dashboard(console: Console, ui_mode: str) -> bool:
    """Resolve ``--ui`` against ``console``: ``dashboard``/``plain`` force the choice;
    ``auto`` (default) uses the dashboard only on a real, non-dumb terminal -- every
    ``CliRunner`` invocation, CI run, and piped/redirected real invocation is a non-tty and
    must fall back to plain output."""
    if ui_mode == "dashboard":
        return True
    if ui_mode == "plain":
        return False
    return console.is_terminal and not console.is_dumb_terminal


class LiveDashboard:
    """Thin wrapper around ``rich.live.Live``, always bound to a caller-supplied ``Console``
    (never its own) so it shares one output stream with the logger's ``RichHandler``."""

    def __init__(self, console: Console) -> None:
        self._live = Live(console=console, transient=False, auto_refresh=False)

    def start(self) -> None:
        self._live.start()

    def stop(self) -> None:
        self._live.stop()

    def update(self, state: DashboardState) -> None:
        self._live.update(render_dashboard(state), refresh=True)

    def __enter__(self) -> LiveDashboard:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
