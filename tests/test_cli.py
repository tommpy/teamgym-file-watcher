"""CLI invocation tests for teamgym-watcher's run/process-once/auth commands.

`run`'s real watch-forever loop (and watchdog's Observer) is never exercised end to end here
-- per prompts/09 that's integration territory. Instead `_run_watch_loop` and `_drain_once`
(the two orchestration functions `run`/`process-once` delegate to) are tested directly with
injected clocks/sleeps/events, and full CLI invocations cover argument parsing, config/auth/API
wiring, and exit codes.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from teamgym_watcher import __version__
from teamgym_watcher.auth import AccessToken, AuthError, save_token_cache
from teamgym_watcher.cli import (
    API_ERROR_EXIT_CODE,
    AUTH_ERROR_EXIT_CODE,
    CONFIG_ERROR_EXIT_CODE,
    EXIFTOOL_ERROR_EXIT_CODE,
    PROCESSING_ERROR_EXIT_CODE,
    _drain_once,
    _run_watch_loop,
    app,
)
from teamgym_watcher.config import Config
from teamgym_watcher.exif import ExifToolUnavailableError, MissingCaptureTimeError
from teamgym_watcher.journal import Journal, JournalStatus, Stats
from teamgym_watcher.pipeline import PipelineDeps
from teamgym_watcher.tag_cache import TagCache
from teamgym_watcher.watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()
API_BASE_URL = "http://localhost:3001/api"


def _valid_args(import_dir: Path, dest_root: Path) -> list[str]:
    return [
        "--api-base-url",
        API_BASE_URL,
        "--competition-id",
        "1",
        "--import-dir",
        str(import_dir),
        "--dest-root",
        str(dest_root),
    ]


def _tags_response(tags: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"data": tags or [], "meta": {"hasNext": False}}


def _mock_tags_endpoint(httpx_mock: HTTPXMock, json: dict[str, object]) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API_BASE_URL}/tags",
        match_params={"page": "1", "limit": "100"},
        json=json,
    )


def _authed_cache_path(tmp_path: Path, make_jwt: Callable[[dict[str, object]], str]) -> Path:
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "exp": 10_000_000_000})
    save_token_cache(cache_path, AccessToken(access_token=jwt, token_type="bearer", scope=""))
    return cache_path


class _FakeApiClient:
    def __init__(self, tags: list[object] | None = None) -> None:
        self._tags = tags or []

    def fetch_all_tags(self, competition_id: int) -> list[object]:
        return list(self._tags)


def _always_missing_capture_time(path: Path, config: Config) -> None:
    raise MissingCaptureTimeError(f"no exif in {path}")


def _pipeline_deps(tmp_path: Path, *, import_dir: Path | None = None) -> PipelineDeps:
    resolved_import_dir = import_dir if import_dir is not None else tmp_path / "import"
    resolved_import_dir.mkdir(parents=True, exist_ok=True)
    dest_root = tmp_path / "dest"
    dest_root.mkdir(parents=True, exist_ok=True)
    config = Config(
        api_base_url="http://api.test",
        competition_id=1,
        import_dir=resolved_import_dir,
        dest_root=dest_root,
    )
    cache = TagCache(1, tag_refresh_seconds=3600, clock=lambda: 100.0)
    cache.refresh(_FakeApiClient([]))
    return PipelineDeps(
        config=config,
        tag_cache=cache,
        api_client=_FakeApiClient([]),  # type: ignore[arg-type]
        journal=Journal(dest_root),
        stats=Stats(),
        clock=lambda: 100.0,
        extract_capture_time=_always_missing_capture_time,  # type: ignore[arg-type]
    )


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
def test_config_error_exits_with_config_error_code(
    command: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    config_path = tmp_path / "empty.toml"
    config_path.write_text("", encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(app, [command, "--config", str(config_path)])

    assert result.exit_code == CONFIG_ERROR_EXIT_CODE
    assert "configuration error" in caplog.text.lower()


@pytest.mark.parametrize("command", ["run", "process-once"])
def test_non_interactive_without_cached_token_exits_with_auth_error(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    monkeypatch.setattr(
        "teamgym_watcher.cli.token_cache_path", lambda: tmp_path / "missing-token.json"
    )

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(
            app, [command, "--non-interactive", *_valid_args(import_dir, dest_root)]
        )

    assert result.exit_code == AUTH_ERROR_EXIT_CODE


@pytest.mark.parametrize("command", ["run", "process-once"])
def test_exiftool_unavailable_exits_with_exiftool_error_code(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"

    def _raise(config: Config) -> None:
        raise ExifToolUnavailableError("exiftool is not installed")

    monkeypatch.setattr("teamgym_watcher.cli.ensure_exiftool_available", _raise)

    result = runner.invoke(app, [command, *_valid_args(import_dir, dest_root)])

    assert result.exit_code == EXIFTOOL_ERROR_EXIT_CODE


@pytest.mark.parametrize("command", ["run", "process-once"])
def test_initial_tag_fetch_failure_exits_with_api_error_code(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
    _mock_tags_endpoint(httpx_mock, {"success": False, "error": "ServerError", "message": "boom"})

    result = runner.invoke(app, [command, "--non-interactive", *_valid_args(import_dir, dest_root)])

    assert result.exit_code == API_ERROR_EXIT_CODE


def test_process_once_happy_path_drains_directory_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
    _mock_tags_endpoint(httpx_mock, _tags_response())
    (import_dir / "IMG_0001.JPG").write_bytes(b"not a real jpeg")

    result = runner.invoke(
        app,
        [
            "process-once",
            "--non-interactive",
            "--stability-seconds",
            "1",
            *_valid_args(import_dir, dest_root),
        ],
    )

    assert result.exit_code == 0
    assert not (import_dir / "IMG_0001.JPG").exists()


def test_process_once_exits_with_processing_error_code_when_errors_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
    _mock_tags_endpoint(httpx_mock, _tags_response())

    def _fake_drain_once(watcher: Watcher, deps: PipelineDeps, **kwargs: object) -> bool:
        deps.stats.record(JournalStatus.ERROR)
        return False

    monkeypatch.setattr("teamgym_watcher.cli._drain_once", _fake_drain_once)

    result = runner.invoke(
        app, ["process-once", "--non-interactive", *_valid_args(import_dir, dest_root)]
    )

    assert result.exit_code == PROCESSING_ERROR_EXIT_CODE


def test_process_once_exits_with_auth_error_code_when_drain_pauses_for_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
    _mock_tags_endpoint(httpx_mock, _tags_response())

    monkeypatch.setattr("teamgym_watcher.cli._drain_once", lambda watcher, deps, **kw: True)

    result = runner.invoke(
        app, ["process-once", "--non-interactive", *_valid_args(import_dir, dest_root)]
    )

    assert result.exit_code == AUTH_ERROR_EXIT_CODE


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


class TestDrainOnce:
    def test_drains_an_existing_stable_file_without_a_real_sleep(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "import"
        (import_dir).mkdir()
        photo = import_dir / "a.JPG"
        photo.write_bytes(b"data")
        clock_box = [0.0]
        watcher = Watcher(import_dir, stability_seconds=3, clock=lambda: clock_box[0])
        deps = _pipeline_deps(tmp_path, import_dir=import_dir)
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock_box[0] += seconds

        auth_paused = _drain_once(watcher, deps, sleep=fake_sleep)

        assert auth_paused is False
        assert not photo.exists()
        assert deps.stats.snapshot().processed == 1
        assert sleeps  # at least one wait for the stability window to clear

    def test_returns_false_immediately_for_an_empty_directory(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        watcher = Watcher(import_dir, stability_seconds=3)
        deps = _pipeline_deps(tmp_path, import_dir=import_dir)

        auth_paused = _drain_once(watcher, deps, sleep=lambda s: pytest.fail("should not sleep"))

        assert auth_paused is False
        assert deps.stats.snapshot().processed == 0


class TestRunWatchLoop:
    def test_returns_immediately_when_stop_event_is_already_set(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        watcher = Watcher(import_dir, stability_seconds=1)
        deps = _pipeline_deps(tmp_path, import_dir=import_dir)
        stop_event = threading.Event()
        stop_event.set()
        sleeps: list[float] = []

        snapshot = _run_watch_loop(watcher, deps, stop_event=stop_event, sleep=sleeps.append)

        assert snapshot.processed == 0
        assert sleeps == []

    def test_processes_a_batch_and_stops_on_the_injected_event(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        photo = import_dir / "a.JPG"
        photo.write_bytes(b"data")
        watcher = Watcher(import_dir, stability_seconds=0)
        watcher.queue.enqueue(photo)
        deps = _pipeline_deps(tmp_path, import_dir=import_dir)
        stop_event = threading.Event()
        sleep_calls = {"n": 0}

        def fake_sleep(seconds: float) -> None:
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 2:
                stop_event.set()

        snapshot = _run_watch_loop(
            watcher,
            deps,
            stop_event=stop_event,
            sleep=fake_sleep,
            poll_interval_seconds=0,
        )

        assert snapshot.processed == 1
        assert snapshot.random == 1
        assert not photo.exists()

    def test_logs_the_periodic_summary_line_on_the_configured_interval(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        watcher = Watcher(import_dir, stability_seconds=1)
        deps = _pipeline_deps(tmp_path, import_dir=import_dir)
        stop_event = threading.Event()
        clock_box = [0.0]
        sleep_calls = {"n": 0}

        def fake_sleep(seconds: float) -> None:
            sleep_calls["n"] += 1
            clock_box[0] += 100.0  # jump straight past the summary interval
            if sleep_calls["n"] >= 2:
                stop_event.set()

        with caplog.at_level(logging.INFO):
            _run_watch_loop(
                watcher,
                deps,
                stop_event=stop_event,
                sleep=fake_sleep,
                clock=lambda: clock_box[0],
                summary_interval_seconds=60.0,
            )

        # One periodic summary line mid-loop, plus the final "Stopping: ..." summary.
        assert caplog.text.count("processed=") >= 2
