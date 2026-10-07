"""Tests for the live terminal status dashboard (prompts/17)."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from teamgym_watcher.dashboard import (
    AUTH_REQUIRED_MESSAGE,
    DashboardState,
    LiveDashboard,
    render_dashboard,
    should_use_dashboard,
)


class _FakeConsole:
    """Duck-typed stand-in for ``rich.console.Console`` exposing only the two attributes
    ``should_use_dashboard`` reads -- avoids relying on real tty/env detection in tests."""

    def __init__(self, *, is_terminal: bool, is_dumb_terminal: bool) -> None:
        self.is_terminal = is_terminal
        self.is_dumb_terminal = is_dumb_terminal


def _state(
    *,
    mode: str = "run",
    dry_run: bool = False,
    processed: int = 10,
    matched: int = 7,
    random_: int = 2,
    errors: int = 1,
    queued: int = 4,
    auth_ok: bool = True,
    auth_message: str | None = None,
) -> DashboardState:
    return DashboardState(
        started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        now=datetime(2026, 1, 1, 1, 2, 3, tzinfo=UTC),
        mode=mode,
        dry_run=dry_run,
        import_dir=Path("/import"),
        dest_root=Path("/dest"),
        api_base_url="http://api.test",
        processed=processed,
        matched=matched,
        random=random_,
        errors=errors,
        queued=queued,
        auth_ok=auth_ok,
        auth_message=auth_message,
    )


def _render_text(state: DashboardState) -> str:
    console = Console(record=True, width=100, force_terminal=True)
    console.print(render_dashboard(state))
    return console.export_text()


class TestRenderDashboard:
    def test_shows_mode_dry_run_badge_and_counts(self) -> None:
        text = _render_text(_state(mode="run", dry_run=True))

        assert "run" in text
        assert "DRY RUN" in text
        assert "processed=10 matched=7 random=2 errors=1 queued=4" in text

    def test_omits_dry_run_badge_when_not_dry_run(self) -> None:
        text = _render_text(_state(dry_run=False))

        assert "DRY RUN" not in text

    def test_shows_authenticated_phrasing_when_auth_ok(self) -> None:
        text = _render_text(_state(auth_ok=True))

        assert "Authenticated" in text
        assert AUTH_REQUIRED_MESSAGE not in text

    def test_shows_auth_required_phrasing_when_not_auth_ok(self) -> None:
        text = _render_text(_state(auth_ok=False, auth_message=AUTH_REQUIRED_MESSAGE))

        assert AUTH_REQUIRED_MESSAGE in text
        assert "Authenticated" not in text


class TestShouldUseDashboard:
    @pytest.mark.parametrize(
        ("is_terminal", "is_dumb_terminal", "ui_mode", "expected"),
        [
            (True, False, "auto", True),  # auto on a real tty
            (False, False, "auto", False),  # auto piped/redirected
            (True, True, "auto", False),  # auto on a dumb terminal
            (False, False, "dashboard", True),  # forced on a non-tty
            (True, False, "plain", False),  # forced plain on a tty
        ],
    )
    def test_resolves_ui_mode(
        self, is_terminal: bool, is_dumb_terminal: bool, ui_mode: str, expected: bool
    ) -> None:
        console = _FakeConsole(is_terminal=is_terminal, is_dumb_terminal=is_dumb_terminal)

        assert should_use_dashboard(console, ui_mode) is expected  # type: ignore[arg-type]


class TestLiveDashboard:
    def test_update_renders_the_latest_state(self) -> None:
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)

        with LiveDashboard(console) as dashboard:
            dashboard.update(_state(queued=1))
            dashboard.update(_state(queued=99))

        output = buffer.getvalue()
        assert "queued=99" in output
