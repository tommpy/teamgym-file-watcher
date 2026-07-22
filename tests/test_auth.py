"""Tests for the GitHub device-flow auth module: no real sleeps, no real network."""

from __future__ import annotations

import io
import logging
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from pytest_httpx import HTTPXMock
from rich.console import Console

from teamgym_watcher.auth import (
    EARLY_EXPIRY_BUFFER_SECONDS,
    AccessToken,
    AuthenticationRequired,
    AuthError,
    DeviceFlowDenied,
    DeviceFlowExpired,
    TokenDecodeError,
    decode_jwt_payload,
    get_valid_token,
    is_token_expired,
    load_token_cache,
    run_device_flow,
    save_token_cache,
)
from teamgym_watcher.config import Config

if TYPE_CHECKING:
    from collections.abc import Callable

API_BASE_URL = "http://api.test"


class FakeClock:
    """A deterministic stand-in for time.monotonic/time.sleep: sleeping advances the clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


def _console() -> Console:
    return Console(file=io.StringIO())


def _run(client: httpx.Client, clock: FakeClock) -> AccessToken:
    return run_device_flow(
        client, API_BASE_URL, console=_console(), sleep=clock.sleep, clock=clock.time
    )


def _start_body(
    *,
    device_code: str = "dev-code",
    user_code: str = "ABCD-EFGH",
    expires_in: int = 900,
    interval: int = 5,
) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "deviceCode": device_code,
            "userCode": user_code,
            "verificationUri": "https://github.com/login/device",
            "verificationUriComplete": f"https://github.com/login/device?user_code={user_code}",
            "expiresIn": expires_in,
            "interval": interval,
        },
    }


def _poll_success_body(token: str = "jwt-token") -> dict[str, object]:
    return {
        "success": True,
        "data": {"accessToken": token, "tokenType": "bearer", "scope": "user:email"},
    }


def _poll_error_body(error: str, message: str = "error") -> dict[str, object]:
    return {"success": False, "error": error, "message": message}


def _config(**overrides: object) -> Config:
    return Config(
        api_base_url=API_BASE_URL,
        import_dir=Path("/import"),
        dest_root=Path("/dest"),
        **overrides,  # type: ignore[arg-type]
    )


class TestRunDeviceFlow:
    def test_happy_path(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_success_body()
        )
        clock = FakeClock()

        with httpx.Client() as client:
            token = _run(client, clock)

        assert token == AccessToken(
            access_token="jwt-token", token_type="bearer", scope="user:email"
        )
        assert clock.sleeps == [5]

    def test_authorization_pending_then_success(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/poll",
            json=_poll_error_body("authorization_pending"),
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_success_body()
        )
        clock = FakeClock()

        with httpx.Client() as client:
            token = _run(client, clock)

        assert token.access_token == "jwt-token"
        assert clock.sleeps == [5, 5]

    def test_slow_down_increases_polling_interval(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body(interval=5)
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_error_body("slow_down")
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_success_body()
        )
        clock = FakeClock()

        with httpx.Client() as client:
            _run(client, clock)

        assert clock.sleeps == [5, 7]

    def test_access_denied_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/poll",
            json=_poll_error_body("access_denied", "User denied the request"),
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(DeviceFlowDenied, match="denied"):
            _run(client, clock)

    def test_expired_token_error_from_server_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/poll",
            json=_poll_error_body("expired_token", "Device code has expired"),
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(DeviceFlowExpired, match="expired"):
            _run(client, clock)

    def test_local_deadline_expires_without_further_polling(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/start",
            json=_start_body(expires_in=10, interval=5),
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/poll",
            json=_poll_error_body("authorization_pending"),
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(DeviceFlowExpired):
            _run(client, clock)

        assert clock.sleeps == [5, 5]

    def test_start_failure_raises_auth_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/start",
            json={"success": False, "message": "GitHub OAuth is not configured"},
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(AuthError, match="not configured"):
            _run(client, clock)

    def test_start_failure_with_500_status_raises_auth_error(self, httpx_mock: HTTPXMock) -> None:
        """The API returns error bodies on non-200 statuses too (see main-api.yaml);
        these must surface as AuthError, not an uncaught httpx.HTTPStatusError."""
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/start",
            status_code=500,
            json={"success": False, "message": "GitHub OAuth is not configured"},
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(AuthError, match="not configured"):
            _run(client, clock)

    def test_poll_failure_with_400_status_raises_auth_error(self, httpx_mock: HTTPXMock) -> None:
        """/device/poll returns 400 for an invalid device code; must not raise
        httpx.HTTPStatusError."""
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{API_BASE_URL}/device/poll",
            status_code=400,
            json={"success": False, "message": "Invalid device code"},
        )
        clock = FakeClock()

        with httpx.Client() as client, pytest.raises(AuthError, match="Invalid device code"):
            _run(client, clock)


class TestJwtDecoding:
    def test_decodes_payload_claims(self, make_jwt: Callable[[dict[str, object]], str]) -> None:
        token = make_jwt({"sub": "123", "email": "a@b.com", "login": "octocat", "exp": 1000})

        payload = decode_jwt_payload(token)

        assert payload == {"sub": "123", "email": "a@b.com", "login": "octocat", "exp": 1000}

    def test_wrong_number_of_parts_raises(self) -> None:
        with pytest.raises(TokenDecodeError, match="3 parts"):
            decode_jwt_payload("only.two")

    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(TokenDecodeError):
            decode_jwt_payload("header.not-valid-base64!!!.sig")

    def test_non_object_payload_raises(self) -> None:
        import base64

        payload_b64 = base64.urlsafe_b64encode(b"[1, 2, 3]").rstrip(b"=").decode("ascii")
        with pytest.raises(TokenDecodeError, match="JSON object"):
            decode_jwt_payload(f"header.{payload_b64}.sig")


class TestIsTokenExpired:
    def test_no_exp_claim_never_expires(self) -> None:
        assert is_token_expired({}, now=999_999_999) is False

    def test_expired_when_within_early_buffer(self) -> None:
        exp = 1_000.0
        now = exp - EARLY_EXPIRY_BUFFER_SECONDS + 1
        assert is_token_expired({"exp": exp}, now=now) is True

    def test_not_expired_when_outside_buffer(self) -> None:
        exp = 1_000.0
        now = exp - EARLY_EXPIRY_BUFFER_SECONDS - 1
        assert is_token_expired({"exp": exp}, now=now) is False

    def test_non_numeric_exp_treated_as_expired(self) -> None:
        assert is_token_expired({"exp": "not-a-number"}, now=0) is True


class TestTokenCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "token.json"
        token = AccessToken(access_token="jwt-token", token_type="bearer", scope="user:email")

        save_token_cache(path, token)
        loaded = load_token_cache(path)

        assert loaded == token

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "token.json"
        token = AccessToken(access_token="jwt-token", token_type="bearer", scope="user:email")

        save_token_cache(path, token)

        assert path.is_file()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
    def test_permissions_restricted_to_owner(self, tmp_path: Path) -> None:
        path = tmp_path / "token.json"
        token = AccessToken(access_token="jwt-token", token_type="bearer", scope="user:email")

        save_token_cache(path, token)

        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_token_cache(tmp_path / "missing.json") is None

    def test_malformed_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "token.json"
        path.write_text("not json", encoding="utf-8")

        assert load_token_cache(path) is None


class TestGetValidToken:
    def test_returns_cached_token_when_fresh(
        self, tmp_path: Path, make_jwt: Callable[[dict[str, object]], str]
    ) -> None:
        cache_path = tmp_path / "token.json"
        jwt = make_jwt({"exp": 10_000})
        save_token_cache(cache_path, AccessToken(access_token=jwt, token_type="bearer", scope=""))

        token = get_valid_token(
            _config(), interactive=False, cache_path=cache_path, now=lambda: 0.0
        )

        assert token == jwt

    def test_non_interactive_raises_when_no_cache(self, tmp_path: Path) -> None:
        with pytest.raises(AuthenticationRequired):
            get_valid_token(_config(), interactive=False, cache_path=tmp_path / "missing.json")

    def test_non_interactive_raises_when_cache_expired(
        self, tmp_path: Path, make_jwt: Callable[[dict[str, object]], str]
    ) -> None:
        cache_path = tmp_path / "token.json"
        jwt = make_jwt({"exp": 1_000})
        save_token_cache(cache_path, AccessToken(access_token=jwt, token_type="bearer", scope=""))

        with pytest.raises(AuthenticationRequired):
            get_valid_token(
                _config(),
                interactive=False,
                cache_path=cache_path,
                now=lambda: 1_000.0,
            )

    def test_interactive_runs_device_flow_and_caches_result(
        self, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        cache_path = tmp_path / "token.json"
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_success_body()
        )
        clock = FakeClock()

        token = get_valid_token(
            _config(),
            interactive=True,
            cache_path=cache_path,
            console=_console(),
            sleep=clock.sleep,
            clock=clock.time,
        )

        assert token == "jwt-token"
        assert load_token_cache(cache_path) == AccessToken(
            access_token="jwt-token", token_type="bearer", scope="user:email"
        )

    def test_expired_cache_triggers_reauth_when_interactive(
        self,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        make_jwt: Callable[[dict[str, object]], str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cache_path = tmp_path / "token.json"
        stale = make_jwt({"exp": 1_000})
        save_token_cache(cache_path, AccessToken(access_token=stale, token_type="bearer", scope=""))
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/start", json=_start_body()
        )
        httpx_mock.add_response(
            method="POST", url=f"{API_BASE_URL}/device/poll", json=_poll_success_body()
        )
        clock = FakeClock()

        with caplog.at_level(logging.INFO):
            token = get_valid_token(
                _config(),
                interactive=True,
                cache_path=cache_path,
                console=_console(),
                sleep=clock.sleep,
                clock=clock.time,
                now=lambda: 1_000.0,
            )

        assert token == "jwt-token"
