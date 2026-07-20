"""CLI invocation tests for the bootstrap stub commands."""

from __future__ import annotations

import logging

import pytest
from typer.testing import CliRunner

from teamgym_watcher import __version__
from teamgym_watcher.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "process-once", "auth"):
        assert command in result.output


def test_version_option_prints_version_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("command", ["run", "process-once", "auth"])
def test_stub_commands_exit_non_zero_and_log_not_implemented(
    command: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        result = runner.invoke(app, [command])

    assert result.exit_code != 0
    assert "not implemented" in caplog.text.lower()


@pytest.mark.parametrize("command", ["run", "process-once", "auth"])
def test_stub_commands_accept_verbose_flag(command: str) -> None:
    result = runner.invoke(app, [command, "--verbose"])

    assert result.exit_code != 0
