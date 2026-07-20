"""CLI invocation tests for the bootstrap stub commands and the `auth` command."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from teamgym_watcher import __version__
from teamgym_watcher.auth import AccessToken, AuthError
from teamgym_watcher.cli import (
    AUTH_ERROR_EXIT_CODE,
    CONFIG_ERROR_EXIT_CODE,
    NOT_IMPLEMENTED_EXIT_CODE,
    app,
)

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()


def _valid_args(import_dir: Path, dest_root: Path) -> list[str]:
    return [
        "--api-base-url",
        "http://localhost:3001/api",
        "--competition-id",
        "1",
        "--import-dir",
        str(import_dir),
        "--dest-root",
        str(dest_root),
    ]


def test_help_lists_all_subcommands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "process-once", "auth"):
        assert command in result.output


def test_version_option_prints_version_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize("command", ["run", "process-once"])
def test_stub_commands_exit_not_implemented_when_config_is_valid(
    command: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(app, [command, *_valid_args(import_dir, dest_root)])

    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE
    assert "not implemented" in caplog.text.lower()


@pytest.mark.parametrize("command", ["run", "process-once"])
def test_stub_commands_accept_verbose_flag(command: str, tmp_path: Path) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"

    result = runner.invoke(app, [command, "--verbose", *_valid_args(import_dir, dest_root)])

    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE


@pytest.mark.parametrize("command", ["run", "process-once", "auth"])
def test_stub_commands_exit_with_config_error_when_config_invalid(
    command: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    config_path = tmp_path / "empty.toml"
    config_path.write_text("", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(app, [command, "--config", str(config_path)])

    assert result.exit_code == CONFIG_ERROR_EXIT_CODE
    assert "configuration error" in caplog.text.lower()


def test_cli_override_takes_precedence_over_toml_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    config_path = tmp_path / "teamgym-watcher.toml"
    config_path.write_text(
        f"""
        api_base_url = "http://localhost:3001/api"
        competition_id = 1
        import_dir = "{import_dir.as_posix()}"
        dest_root = "{dest_root.as_posix()}"
        """,
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO):
        result = runner.invoke(
            app,
            [
                "process-once",
                "--config",
                str(config_path),
                "--competition-id",
                "42",
            ],
        )

    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE
    assert "competition 42" in caplog.text.lower()


def test_auth_command_saves_token_and_prints_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "email": "octocat@example.com", "exp": 10_000})
    token = AccessToken(access_token=jwt, token_type="bearer", scope="user:email")

    monkeypatch.setattr("teamgym_watcher.cli.run_device_flow", lambda client, api_base_url: token)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

    with caplog.at_level(logging.INFO):
        result = runner.invoke(app, ["auth", *_valid_args(import_dir, dest_root)])

    assert result.exit_code == 0
    assert "octocat" in caplog.text
    assert cache_path.is_file()


def test_auth_command_exits_with_auth_error_code_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"

    def _raise(client: object, api_base_url: str) -> AccessToken:
        raise AuthError("Authorization was denied.")

    monkeypatch.setattr("teamgym_watcher.cli.run_device_flow", _raise)

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(app, ["auth", *_valid_args(import_dir, dest_root)])

    assert result.exit_code == AUTH_ERROR_EXIT_CODE
    assert "authentication failed" in caplog.text.lower()
