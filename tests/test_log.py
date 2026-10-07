"""Tests for setup_logging handler attachment."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

from teamgym_watcher.log import setup_logging


def _handler_names() -> list[str]:
    return [h.name for h in logging.getLogger().handlers if h.name is not None]


def test_setup_logging_attaches_handlers_idempotently(tmp_path: Path) -> None:
    log_file = tmp_path / "watcher.log"

    setup_logging(verbose=True, log_file=log_file)
    setup_logging(verbose=True, log_file=log_file)

    names = _handler_names()
    assert names.count("teamgym_watcher.console") == 1
    assert names.count("teamgym_watcher.file") == 1


def test_setup_logging_without_log_file_adds_only_console_handler() -> None:
    setup_logging(verbose=False, log_file=None)

    names = _handler_names()
    assert "teamgym_watcher.console" in names
    assert "teamgym_watcher.file" not in names


def test_setup_logging_creates_log_file_parent_dir(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "watcher.log"

    setup_logging(verbose=False, log_file=log_file)

    assert log_file.parent.is_dir()


def test_setup_logging_with_console_attaches_handler_using_that_instance() -> None:
    console = Console(file=None)

    setup_logging(verbose=False, log_file=None, console=console)

    handler = next(h for h in logging.getLogger().handlers if h.name == "teamgym_watcher.console")
    assert handler.console is console  # type: ignore[attr-defined]


def test_setup_logging_closes_previous_file_handler_on_replacement(tmp_path: Path) -> None:
    first_log_file = tmp_path / "first.log"
    setup_logging(verbose=True, log_file=first_log_file)
    first_handler = next(
        h for h in logging.getLogger().handlers if h.name == "teamgym_watcher.file"
    )

    setup_logging(verbose=True, log_file=tmp_path / "second.log")

    assert first_handler.stream is None or first_handler.stream.closed
    first_log_file.unlink()  # would raise/lock on Windows if the handler were still open
