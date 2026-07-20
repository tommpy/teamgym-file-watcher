"""GitHub OAuth device-flow authentication (RFC 8628) against the teamgym-sorter API.

Ports the behaviour of ``ApiAuthentication.lua`` from the Lightroom plugin: start the
device flow, poll until the user authorizes on GitHub, cache the resulting JWT, and treat
it as expired five minutes before its real expiry (the plugin's clock-skew buffer). The
JWT payload is decoded (base64url, unverified — same as the plugin) purely to read ``exp``
and the identity claims; the server remains the source of truth for validity.

The watcher loop (later steps) calls :func:`get_valid_token` with ``interactive=False`` on
every batch. When it raises :class:`AuthenticationRequired`, the loop must pause its
processing queue and log the re-auth instruction (run ``teamgym-watcher auth``) loudly
every 60 seconds until a fresh token is cached, rather than crashing or dropping photos.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import httpx
import platformdirs
from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from teamgym_watcher.config import Config

logger = logging.getLogger(__name__)

TOKEN_CACHE_FILENAME: Final = "token.json"

# ApiConfig.AUTH.MIN_POLLING_INTERVAL
MIN_POLLING_INTERVAL_SECONDS: Final = 5
# The plugin's "slow_down" backoff step.
SLOW_DOWN_INCREMENT_SECONDS: Final = 2
# The plugin's 5-minute early-expiry buffer, to account for clock skew.
EARLY_EXPIRY_BUFFER_SECONDS: Final = 5 * 60
HTTP_TIMEOUT_SECONDS: Final = 30.0


class AuthError(Exception):
    """Base class for device-flow authentication failures."""


class DeviceFlowDenied(AuthError):
    """The user denied the device authorization request."""


class DeviceFlowExpired(AuthError):
    """The device code expired before authorization completed."""


class TokenDecodeError(AuthError):
    """A JWT could not be decoded. Callers generally treat this as 'expired'."""


class AuthenticationRequired(AuthError):
    """No fresh cached token exists and interactive re-authentication is not allowed.

    Raised by :func:`get_valid_token` when called with ``interactive=False``. The watcher
    loop must catch this, pause its queue, and log the re-auth instruction loudly every
    60 seconds until ``teamgym-watcher auth`` produces a fresh cached token.
    """


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    """Response from ``POST /device/start``."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A JWT issued by ``POST /device/poll``, as stored in the token cache."""

    access_token: str
    token_type: str
    scope: str


def token_cache_path() -> Path:
    """Location of the cached-token JSON file, under the platform config dir."""
    config_dir = platformdirs.user_config_dir("teamgym-watcher", appauthor=False)
    return Path(config_dir) / TOKEN_CACHE_FILENAME


def decode_jwt_payload(token: str) -> dict[str, object]:
    """Decode a JWT's payload without verifying its signature (matches the plugin)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenDecodeError(f"Invalid JWT format: expected 3 parts, got {len(parts)}.")

    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
    except (binascii.Error, ValueError) as exc:
        raise TokenDecodeError(f"Failed to decode JWT payload: {exc}") from exc

    if not isinstance(payload, dict):
        raise TokenDecodeError("JWT payload is not a JSON object.")
    return payload


def is_token_expired(payload: Mapping[str, object], *, now: float) -> bool:
    """True if ``payload["exp"]`` is within :data:`EARLY_EXPIRY_BUFFER_SECONDS` of ``now``.

    A payload with no ``exp`` claim is treated as never expiring, matching the plugin.
    """
    exp = payload.get("exp")
    if exp is None:
        return False
    if not isinstance(exp, int | float):
        return True
    return (now + EARLY_EXPIRY_BUFFER_SECONDS) >= exp


def load_token_cache(path: Path) -> AccessToken | None:
    """Read a cached token, or ``None`` if the file is absent or unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AccessToken(
            access_token=data["accessToken"],
            token_type=data["tokenType"],
            scope=data.get("scope", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Token cache at %s is unreadable; ignoring it.", path)
        return None


def save_token_cache(path: Path, token: AccessToken) -> None:
    """Write the token cache as JSON, restricting permissions to 0600 where supported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "accessToken": token.access_token,
        "tokenType": token.token_type,
        "scope": token.scope,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _start_device_flow(client: httpx.Client, api_base_url: str) -> DeviceAuthorization:
    response = client.post(f"{api_base_url}/device/start", json={})
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise AuthError(body.get("message", "Failed to start the device flow."))

    data = body["data"]
    return DeviceAuthorization(
        device_code=data["deviceCode"],
        user_code=data["userCode"],
        verification_uri=data["verificationUri"],
        verification_uri_complete=data["verificationUriComplete"],
        expires_in=data["expiresIn"],
        interval=data["interval"],
    )


_PollStatus = Literal["pending", "slow_down"]


def _poll_device_authorization(
    client: httpx.Client, api_base_url: str, device_code: str
) -> AccessToken | _PollStatus:
    response = client.post(f"{api_base_url}/device/poll", json={"deviceCode": device_code})
    response.raise_for_status()
    body = response.json()

    if body.get("success"):
        data = body["data"]
        return AccessToken(
            access_token=data["accessToken"],
            token_type=data["tokenType"],
            scope=data.get("scope", ""),
        )

    error = body.get("error")
    if error == "authorization_pending":
        return "pending"
    if error == "slow_down":
        return "slow_down"
    if error == "access_denied":
        raise DeviceFlowDenied(body.get("message", "Authorization was denied."))
    if error == "expired_token":
        raise DeviceFlowExpired(body.get("message", "Device code expired."))
    raise AuthError(body.get("message", f"Unknown device flow error: {error!r}"))


def _display_device_code(authorization: DeviceAuthorization, console: Console) -> None:
    minutes = authorization.expires_in // 60
    plain = (
        f"To authenticate, visit {authorization.verification_uri_complete} "
        f"and enter code {authorization.user_code} (expires in {minutes} minutes)."
    )
    logger.info(plain)
    console.print(
        Panel(
            f"[bold]{authorization.user_code}[/bold]\n\n{authorization.verification_uri_complete}",
            title="TeamGym API Authentication",
            subtitle=f"Code expires in {minutes} minutes",
        )
    )


def run_device_flow(
    client: httpx.Client,
    api_base_url: str,
    *,
    console: Console | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AccessToken:
    """Run the device flow to completion: start, display the code, and poll until done.

    ``sleep`` and ``clock`` are injectable so tests never perform a real sleep.
    """
    authorization = _start_device_flow(client, api_base_url)
    _display_device_code(authorization, console if console is not None else Console())

    interval = max(authorization.interval, MIN_POLLING_INTERVAL_SECONDS)
    deadline = clock() + authorization.expires_in

    while True:
        sleep(interval)
        if clock() >= deadline:
            raise DeviceFlowExpired(
                f"Device code expired after {authorization.expires_in} seconds "
                "without authorization."
            )

        result = _poll_device_authorization(client, api_base_url, authorization.device_code)
        if isinstance(result, AccessToken):
            logger.info("Device flow authorization granted.")
            return result
        if result == "slow_down":
            interval += SLOW_DOWN_INCREMENT_SECONDS


def get_valid_token(
    config: Config,
    *,
    interactive: bool,
    cache_path: Path | None = None,
    client: httpx.Client | None = None,
    console: Console | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], float] = time.time,
) -> str:
    """Return a valid JWT, from cache if fresh, else via an interactive device flow.

    If the cached token is missing or expired (within the 5-minute buffer) and
    ``interactive`` is ``False``, raises :class:`AuthenticationRequired` instead of
    prompting — used by the unattended watcher loop, which pauses and logs instead of
    blocking on user input.
    """
    path = cache_path if cache_path is not None else token_cache_path()
    cached = load_token_cache(path)
    if cached is not None:
        try:
            payload = decode_jwt_payload(cached.access_token)
        except TokenDecodeError:
            payload = None
        if payload is not None and not is_token_expired(payload, now=now()):
            return cached.access_token

    if not interactive:
        raise AuthenticationRequired(
            "No valid cached token and running non-interactively. "
            "Run `teamgym-watcher auth` to re-authenticate."
        )

    owns_client = client is None
    http_client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        token = run_device_flow(
            http_client, config.api_base_url, console=console, sleep=sleep, clock=clock
        )
    finally:
        if owns_client:
            http_client.close()

    save_token_cache(path, token)
    return token.access_token
