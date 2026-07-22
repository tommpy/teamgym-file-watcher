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
from typer.testing import CliRunner

from teamgym_watcher import __version__
from teamgym_watcher.auth import AccessToken, AuthError, save_token_cache
from teamgym_watcher.cli import (
    AUTH_ERROR_EXIT_CODE,
    CONFIG_ERROR_EXIT_CODE,
    EXIFTOOL_ERROR_EXIT_CODE,
    PROCESSING_ERROR_EXIT_CODE,
    _drain_once,
    _process_ready_groups,
    _run_watch_loop,
    app,
)
from teamgym_watcher.config import (
    PROD_API_BASE_URL,
    STAGING_API_BASE_URL,
    Config,
)
from teamgym_watcher.exif import ExifToolUnavailableError, MissingCaptureTimeError
from teamgym_watcher.journal import Journal, JournalStatus, Stats
from teamgym_watcher.mover import PhotoGroup
from teamgym_watcher.pipeline import BatchResult, PipelineDeps, process_batch
from teamgym_watcher.watcher import Watcher

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()
API_BASE_URL = "http://localhost:3001/api"


def _required_args(import_dir: Path, dest_root: Path) -> list[str]:
    return [
        "--import-dir",
        str(import_dir),
        "--dest-root",
        str(dest_root),
    ]


def _valid_args(import_dir: Path, dest_root: Path) -> list[str]:
    return ["--api-base-url", API_BASE_URL, *_required_args(import_dir, dest_root)]


def _tags_response(tags: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"data": tags or [], "meta": {"hasNext": False}}


def _authed_cache_path(tmp_path: Path, make_jwt: Callable[[dict[str, object]], str]) -> Path:
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "exp": 10_000_000_000})
    save_token_cache(cache_path, AccessToken(access_token=jwt, token_type="bearer", scope=""))
    return cache_path


class _FakeApiClient:
    def __init__(self, tags: list[object] | None = None) -> None:
        self._tags = tags or []

    def fetch_tags_window(self, start: object, end: object) -> list[object]:
        return list(self._tags)


def _always_missing_capture_time(path: Path, config: Config) -> None:
    raise MissingCaptureTimeError(f"no exif in {path}")


def _pipeline_deps(
    tmp_path: Path, *, import_dir: Path | None = None, batch_size: int = 25
) -> PipelineDeps:
    resolved_import_dir = import_dir if import_dir is not None else tmp_path / "import"
    resolved_import_dir.mkdir(parents=True, exist_ok=True)
    dest_root = tmp_path / "dest"
    dest_root.mkdir(parents=True, exist_ok=True)
    config = Config(
        api_base_url="http://api.test",
        import_dir=resolved_import_dir,
        dest_root=dest_root,
        batch_size=batch_size,
    )
    return PipelineDeps(
        config=config,
        api_client=_FakeApiClient([]),  # type: ignore[arg-type]
        journal=Journal(dest_root),
        stats=Stats(),
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
def test_local_and_staging_conflict_exits_with_config_error_code(
    command: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"

    with caplog.at_level(logging.ERROR):
        result = runner.invoke(
            app,
            [command, "--local", "--staging", *_required_args(import_dir, dest_root)],
        )

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


def test_process_once_happy_path_drains_directory_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
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


def test_process_once_local_flag_uses_local_api_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

    result = runner.invoke(
        app,
        [
            "process-once",
            "--non-interactive",
            "--local",
            "--stability-seconds",
            "1",
            *_required_args(import_dir, dest_root),
        ],
    )

    assert result.exit_code == 0


def test_process_once_exits_with_processing_error_code_when_errors_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

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
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

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


def test_auth_command_staging_flag_uses_staging_api_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "exp": 10_000})
    token = AccessToken(access_token=jwt, token_type="bearer", scope="")
    captured: dict[str, str] = {}

    def _run_device_flow(client: object, api_base_url: str) -> AccessToken:
        captured["api_base_url"] = api_base_url
        return token

    monkeypatch.setattr("teamgym_watcher.cli.run_device_flow", _run_device_flow)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

    result = runner.invoke(
        app,
        ["auth", "--staging", *_required_args(import_dir, dest_root)],
    )

    assert result.exit_code == 0
    assert captured["api_base_url"] == STAGING_API_BASE_URL


def test_auth_command_defaults_to_prod_api_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
) -> None:
    import_dir = tmp_path / "import"
    import_dir.mkdir()
    dest_root = tmp_path / "dest"
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "exp": 10_000})
    token = AccessToken(access_token=jwt, token_type="bearer", scope="")
    captured: dict[str, str] = {}

    def _run_device_flow(client: object, api_base_url: str) -> AccessToken:
        captured["api_base_url"] = api_base_url
        return token

    monkeypatch.setattr("teamgym_watcher.cli.run_device_flow", _run_device_flow)
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)

    result = runner.invoke(app, ["auth", *_required_args(import_dir, dest_root)])

    assert result.exit_code == 0
    assert captured["api_base_url"] == PROD_API_BASE_URL


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


class TestProcessReadyGroups:
    """PLAN.md's "Batching / API economy": ready groups must be drained in
    ``config.batch_size``-sized chunks, not all at once, even when a rescan hands back many
    more files than that in a single poll."""

    def _groups(self, import_dir: Path, count: int) -> list[PhotoGroup]:
        groups = []
        for n in range(count):
            photo = import_dir / f"{n}.JPG"
            photo.write_bytes(b"data")
            groups.append(PhotoGroup(files=(photo,), capture_time_file=photo))
        return groups

    def test_splits_into_batch_size_chunks_and_logs_per_chunk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        deps = _pipeline_deps(tmp_path, import_dir=import_dir, batch_size=2)
        watcher = Watcher(import_dir, stability_seconds=0)
        groups = self._groups(import_dir, 5)

        chunk_sizes: list[int] = []

        def spy(chunk: object, deps_: PipelineDeps) -> BatchResult:
            chunk = list(chunk)  # type: ignore[arg-type]
            chunk_sizes.append(len(chunk))
            return process_batch(chunk, deps_)  # type: ignore[arg-type]

        monkeypatch.setattr("teamgym_watcher.cli.process_batch", spy)

        with caplog.at_level(logging.INFO):
            pending, auth_paused = _process_ready_groups(watcher, groups, deps)

        assert auth_paused is False
        assert pending == []
        assert chunk_sizes == [2, 2, 1]  # 5 groups, batch_size=2 -> three chunks
        assert deps.stats.snapshot().processed == 5
        assert caplog.text.count("processed=") == 3  # one summary line per chunk

    def test_auth_pause_mid_chunking_returns_remaining_chunks_as_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        deps = _pipeline_deps(tmp_path, import_dir=import_dir, batch_size=2)
        watcher = Watcher(import_dir, stability_seconds=0)
        groups = self._groups(import_dir, 5)
        call_count = {"n": 0}

        def fake_process_batch(chunk: object, deps_: PipelineDeps) -> BatchResult:
            call_count["n"] += 1
            chunk_list = list(chunk)  # type: ignore[arg-type]
            if call_count["n"] == 2:
                return BatchResult(outcomes=(), pending=tuple(chunk_list), auth_paused=True)
            return BatchResult(outcomes=(), pending=(), auth_paused=False)

        monkeypatch.setattr("teamgym_watcher.cli.process_batch", fake_process_batch)

        pending, auth_paused = _process_ready_groups(watcher, groups, deps)

        assert auth_paused is True
        # Second chunk (groups[2:4]) pauses in full, plus the third chunk (groups[4:]) that
        # was never attempted -- neither may be dropped.
        assert pending == groups[2:]
        assert call_count["n"] == 2  # the third chunk must not be attempted after the pause
