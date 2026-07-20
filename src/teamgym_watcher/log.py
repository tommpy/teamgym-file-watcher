"""Logging setup: a Rich console handler plus an optional rotating file handler."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_CONSOLE_HANDLER_NAME = "teamgym_watcher.console"
_FILE_HANDLER_NAME = "teamgym_watcher.file"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logging(verbose: bool, log_file: Path | None = None) -> None:
    """Configure the root logger with a Rich console handler and optional rotating file handler.

    Safe to call repeatedly (e.g. once per CLI stub invocation): handlers previously
    installed by this function are replaced rather than duplicated, so the root logger
    never accumulates extra output on repeated calls.
    """
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if handler.name in (_CONSOLE_HANDLER_NAME, _FILE_HANDLER_NAME):
            root.removeHandler(handler)

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    console_handler.set_name(_CONSOLE_HANDLER_NAME)
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.set_name(_FILE_HANDLER_NAME)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)
