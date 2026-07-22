"""GitHub OAuth device-flow authentication (RFC 8628) against the teamgym-sorter API.

Ports the behaviour of ``ApiAuthentication.lua`` from the Lightroom plugin: start the
device flow, poll until the user authorizes on GitHub, cache the resulting JWT, and treat
it as expired five minutes before its real expiry (the plugin's clock-skew buffer). The
JWT payload is decoded (base64url, unverified — same as the plugin) purely to read ``exp``
and the identity claims; the server remains the source of truth for validity.

The server also issues an opaque **refresh token** alongside the JWT (30-day absolute cap,
rotated on every use). :func:`get_valid_token` uses it to obtain a new access token
silently via :func:`refresh_access_token` whenever the cached JWT has expired — this
happens regardless of ``interactive``, since it's exactly what lets the unattended watcher
loop keep going without an operator re-running the device flow roughly once an hour.
:class:`AuthenticationRequired` is now only raised when silent refresh isn't possible: no
refresh token cached, the 30-day cap reached, reuse was detected (the whole token family
was revoked), or the user was de-authorized server-side. The watcher loop catches it,
pauses its processing queue, and logs the re-auth instruction (run ``teamgym-watcher
auth``) loudly every 60 seconds until a fresh token is cached, rather than crashing or
dropping photos.
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
from typing import TYPE_CHECKING, Any, Final, Literal

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
    """A JWT issued by ``POST /device/poll`` or ``POST /device/refresh``, as cached.

    ``refresh_token``/``refresh_token_expires_at`` are ``None`` for a cache written before
    refresh-token support existed, or if the server ever omits them -- callers must treat
    that as "no silent refresh available", not a hard error. ``refresh_token_expires_at``
    is an absolute instant (seconds since epoch, comparable to ``time.time()``), computed
    once as ``now + refreshTokenExpiresIn`` at the moment the token was received -- the
    30-day cap is absolute and does not slide on rotation, so storing the absolute instant
    avoids re-deriving that distinction later.
    """

    access_token: str
    token_type: str
    scope: str
    refresh_token: str | None = None
    refresh_token_expires_at: float | None = None


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
    """Read a cached token, or ``None`` if the file is absent or unreadable.

    A cache written before refresh-token support existed has no ``refreshToken``/
    ``refreshTokenExpiresAt`` keys; those are read via ``.get`` and default to ``None``
    ("no refresh available"), not a load failure.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AccessToken(
            access_token=data["accessToken"],
            token_type=data["tokenType"],
            scope=data.get("scope", ""),
            refresh_token=data.get("refreshToken"),
            refresh_token_expires_at=data.get("refreshTokenExpiresAt"),
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
        "refreshToken": token.refresh_token,
        "refreshTokenExpiresAt": token.refresh_token_expires_at,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _clear_token_cache(path: Path) -> None:
    """Delete the token cache, e.g. once a refresh token is confirmed dead server-side."""
    path.unlink(missing_ok=True)


def _access_token_from_data(
    data: Mapping[str, Any], *, now: float, fallback_scope: str = ""
) -> AccessToken:
    """Build an :class:`AccessToken` from a ``/device/poll`` or ``/device/refresh`` body.

    ``RefreshResponse`` (unlike ``AuthorizationSuccess``) has no ``scope`` field, since
    rotation doesn't change it -- ``fallback_scope`` lets the caller carry the previously
    cached scope forward in that case.
    """
    refresh_token = data.get("refreshToken")
    refresh_token_expires_in = data.get("refreshTokenExpiresIn")
    refresh_token_expires_at = (
        now + refresh_token_expires_in
        if refresh_token is not None and isinstance(refresh_token_expires_in, int | float)
        else None
    )
    return AccessToken(
        access_token=data["accessToken"],
        token_type=data["tokenType"],
        scope=data.get("scope", fallback_scope),
        refresh_token=refresh_token,
        refresh_token_expires_at=refresh_token_expires_at,
    )


def _parse_response_body(response: httpx.Response) -> dict[str, Any]:
    """Parse a device-flow JSON body regardless of HTTP status code.

    The sorter API returns its ``{success, error, message}`` error shape on 400/500
    responses, not just 200 (see ``main-api.yaml``) — matching the plugin's
    ``LrHttp``-based ``makeHttpRequest``, which never checks the status code and just
    parses whatever body comes back. Calling ``raise_for_status()`` here would turn a
    clean, server-provided error message into an uncaught ``httpx.HTTPStatusError``.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError(f"Invalid response from the server: {exc}") from exc
    if not isinstance(body, dict):
        raise AuthError("Invalid response from the server: expected a JSON object.")
    return body


def _start_device_flow(client: httpx.Client, api_base_url: str) -> DeviceAuthorization:
    response = client.post(f"{api_base_url}/device/start", json={})
    body = _parse_response_body(response)
    if not body.get("success"):
        raise AuthError(str(body.get("message", "Failed to start the device flow.")))

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
    client: httpx.Client, api_base_url: str, device_code: str, *, now: float
) -> AccessToken | _PollStatus:
    response = client.post(f"{api_base_url}/device/poll", json={"deviceCode": device_code})
    body = _parse_response_body(response)

    if body.get("success"):
        return _access_token_from_data(body["data"], now=now)

    error = body.get("error")
    if error == "authorization_pending":
        return "pending"
    if error == "slow_down":
        return "slow_down"
    if error == "access_denied":
        raise DeviceFlowDenied(str(body.get("message", "Authorization was denied.")))
    if error == "expired_token":
        raise DeviceFlowExpired(str(body.get("message", "Device code expired.")))
    raise AuthError(str(body.get("message", f"Unknown device flow error: {error!r}")))


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
    now: Callable[[], float] = time.time,
) -> AccessToken:
    """Run the device flow to completion: start, display the code, and poll until done.

    ``sleep`` and ``clock`` are injectable so tests never perform a real sleep. ``clock``
    paces the polling interval/deadline (monotonic); ``now`` is the wall clock used to
    compute the refresh token's absolute expiry the moment it's received.
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

        result = _poll_device_authorization(
            client, api_base_url, authorization.device_code, now=now()
        )
        if isinstance(result, AccessToken):
            logger.info("Device flow authorization granted.")
            return result
        if result == "slow_down":
            interval += SLOW_DOWN_INCREMENT_SECONDS


def refresh_access_token(
    config: Config,
    cached: AccessToken,
    *,
    cache_path: Path,
    client: httpx.Client | None = None,
    now: Callable[[], float] = time.time,
) -> AccessToken:
    """Exchange ``cached.refresh_token`` for a new access token, rotating the refresh token.

    ``POST /device/refresh`` consumes the presented refresh token and returns a new one
    that must replace it everywhere -- on success the rotated pair is written to
    ``cache_path`` and returned.

    Raises :class:`AuthenticationRequired` if the refresh token is dead: ``invalid_grant``
    (unknown, expired, malformed, or already-used -- reuse revokes the whole token family)
    or ``access_denied`` (the user's email is no longer authorized). Either way the cache
    is cleared so a dead refresh token is never retried forever; the caller's only recovery
    is re-running the device flow. A network or response-parse failure is *not* treated as
    de-authorization -- it surfaces as :class:`AuthError` so a transient blip doesn't get
    mistaken for a revoked session.
    """
    if cached.refresh_token is None:
        raise AuthenticationRequired(
            "No refresh token available. Run `teamgym-watcher auth` to re-authenticate."
        )

    owns_client = client is None
    http_client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        try:
            response = http_client.post(
                f"{config.api_base_url}/device/refresh",
                json={"refreshToken": cached.refresh_token},
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"Network error while refreshing the access token: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()

    body = _parse_response_body(response)
    if not body.get("success"):
        error = body.get("error")
        message = str(body.get("message", "Failed to refresh the access token."))
        if error == "invalid_grant":
            _clear_token_cache(cache_path)
            raise AuthenticationRequired(
                f"Refresh token is no longer valid ({message}). "
                "Run `teamgym-watcher auth` to re-authenticate."
            )
        if error == "access_denied":
            _clear_token_cache(cache_path)
            raise AuthenticationRequired(
                f"User is no longer authorized ({message}). "
                "Run `teamgym-watcher auth` to re-authenticate."
            )
        raise AuthError(message)

    new_token = _access_token_from_data(body["data"], now=now(), fallback_scope=cached.scope)
    save_token_cache(cache_path, new_token)
    return new_token


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
    """Return a valid JWT: from cache if fresh, else via silent refresh, else interactively.

    Preference order: (1) the cached JWT if it isn't expired; (2) a silently refreshed
    access token if the JWT is expired (or undecodable) but a refresh token is cached and
    its absolute expiry hasn't passed yet -- this happens **regardless of** ``interactive``,
    since it's exactly what lets the unattended watcher loop keep going without operator
    involvement; (3) an interactive device flow if ``interactive`` is ``True``. If none of
    those apply -- no refresh token, past the 30-day cap, or the refresh token turned out
    to be dead (reuse detected or de-authorized) -- raises :class:`AuthenticationRequired`
    instead of prompting, used by the unattended watcher loop, which pauses and logs
    instead of blocking on user input.
    """
    path = cache_path if cache_path is not None else token_cache_path()
    cached = load_token_cache(path)
    current_time = now()
    if cached is not None:
        try:
            payload = decode_jwt_payload(cached.access_token)
        except TokenDecodeError:
            payload = None
        if payload is not None and not is_token_expired(payload, now=current_time):
            return cached.access_token

        if (
            cached.refresh_token is not None
            and cached.refresh_token_expires_at is not None
            and cached.refresh_token_expires_at > current_time
        ):
            try:
                refreshed = refresh_access_token(
                    config, cached, cache_path=path, client=client, now=now
                )
            except AuthenticationRequired:
                pass  # No silent refresh available; fall through to interactive/raise below.
            else:
                return refreshed.access_token

    if not interactive:
        raise AuthenticationRequired(
            "No valid cached token and running non-interactively. "
            "Run `teamgym-watcher auth` to re-authenticate."
        )

    owns_client = client is None
    http_client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        token = run_device_flow(
            http_client, config.api_base_url, console=console, sleep=sleep, clock=clock, now=now
        )
    finally:
        if owns_client:
            http_client.close()

    save_token_cache(path, token)
    return token.access_token


def logout(api_base_url: str, cache_path: Path, *, client: httpx.Client | None = None) -> None:
    """Revoke the cached refresh token server-side (best-effort) and clear the local cache.

    The server round-trip is best-effort: whether or not it succeeds, the operator's real
    goal -- this machine can no longer silently refresh -- is achieved by deleting the local
    cache regardless, so failures are logged, not raised. Idempotent: a missing cache (no
    refresh token to revoke, e.g. already logged out) is a no-op success, matching
    ``/device/logout``'s own idempotency for an unknown/already-revoked token.
    """
    cached = load_token_cache(cache_path)
    if cached is not None and cached.refresh_token is not None:
        owns_client = client is None
        http_client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        try:
            response = http_client.post(
                f"{api_base_url}/device/logout", json={"refreshToken": cached.refresh_token}
            )
            _parse_response_body(response)
        except (httpx.HTTPError, AuthError) as exc:
            logger.warning("Failed to revoke the refresh token on the server: %s", exc)
        finally:
            if owns_client:
                http_client.close()

    _clear_token_cache(cache_path)
