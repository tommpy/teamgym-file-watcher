# Step 16 — Refresh-token support in the device-flow auth

Read `PLAN.md`, `src/teamgym_watcher/auth.py`, `src/teamgym_watcher/cli.py`, and the
`## Authentication` section plus the `/device/refresh`, `/device/logout` and
`AuthorizationSuccess`/`RefreshResponse`/`RefreshErrorResponse` schemas in
`../teamgym-sorter/docs/api/main-api.yaml`. The sorter API just landed refresh tokens
(`../teamgym-sorter/prompts/39_device_flow_refresh_tokens.md`); this step teaches the watcher
to use them.

## Why

Today `auth.py` mints a 1-hour JWT and nothing else. When it expires, `get_valid_token`
either runs the full interactive device flow (`interactive=True`) or raises
`AuthenticationRequired` (`interactive=False`), which makes the unattended watcher loop pause
its queue and nag the operator to run `teamgym-watcher auth` roughly once an hour. The server
now issues a **refresh token** alongside the JWT, so the watcher can obtain a new access token
silently — reducing the interactive GitHub step to at most once every 30 days.

## Server contract (from `main-api.yaml` — do not re-derive from server code)

- `POST /device/poll` success `data` now additionally carries `refreshToken` (opaque string,
  `<tokenId>.<secret>` — **not** a JWT, never decode it) and `refreshTokenExpiresIn` (seconds
  remaining on a hard 30-day family cap). Existing fields (`accessToken`, `tokenType`, `scope`,
  and now `expiresIn`) are unchanged.
- `POST /device/refresh` with body `{"refreshToken": "..."}`, **no** bearer auth, returns
  `{success: true, data: {accessToken, tokenType, expiresIn, refreshToken,
  refreshTokenExpiresIn}}`. **Rotation**: the presented refresh token is consumed and a *new*
  one is returned — always persist the new one.
- Failure envelope is the usual `{success: false, error, message}`:
  - `401 invalid_grant` — token unknown, expired, malformed, or already used (reuse detection
    revoked the whole family). Unrecoverable: the operator must re-run the device flow.
  - `403 access_denied` — token valid but the user's email is no longer authorized.
    Unrecoverable the same way.
- `POST /device/logout` with body `{"refreshToken": "..."}`, no bearer auth, returns
  `200 {success: true}`. Idempotent (unknown/already-revoked token still succeeds).
- The 30-day cap is **absolute** and does **not** slide on rotation: `refreshTokenExpiresIn`
  counts down toward the original issue time. Once it reaches 0, refresh will fail and a full
  device flow is required.

## Requirements

1. **Persist the refresh token.** Extend `AccessToken` (or add a sibling dataclass — your call,
   but keep one cache record) so the cache at `token.json` also stores `refreshToken` and an
   **absolute** `refreshTokenExpiresAt` (compute once as `now + refreshTokenExpiresIn` at the
   moment it is received, mirroring how the JWT's `exp` is used — storing the absolute instant
   avoids re-deriving the sliding-vs-absolute distinction later). Update `save_token_cache` /
   `load_token_cache` accordingly. A cache written by the old version (no refresh fields) must
   still load — treat a missing refresh token as "no refresh available", not a hard error.
2. **Capture refresh fields on poll.** `_poll_device_authorization` and `run_device_flow` must
   read `refreshToken` and `refreshTokenExpiresIn` from the poll success body and carry them
   into the returned/saved token. Keep the existing `_parse_response_body` (status-code-agnostic)
   behaviour — do not add `raise_for_status()`.
3. **New `refresh_access_token(config, cached, *, cache_path, client=..., now=...)`.** POSTs
   `{"refreshToken": ...}` to `{api}/device/refresh`, and on success writes the rotated pair to
   the cache and returns the new `AccessToken`. Map failures to the existing exception
   vocabulary:
   - `error == "invalid_grant"` → raise `AuthenticationRequired` (family is dead; the presented
     refresh token, and any cached one, must be discarded — clear/overwrite the cache so a stale
     dead token isn't retried forever).
   - `error == "access_denied"` → raise `AuthenticationRequired` with a message that says the
     user is no longer authorized (same recovery path: re-run `auth`).
   - Network/parse failure → let it surface as `AuthError` (the loop already handles transient
     API failure; a refresh blip should not be treated as de-auth).
4. **Rewire `get_valid_token`** to prefer silent refresh over both re-prompting *and* raising:
   - cached access token fresh → return it (unchanged);
   - access token expired (or undecodable) **but** a refresh token is present and
     `refreshTokenExpiresAt` is still in the future → call `refresh_access_token` and return the
     new access token. **This happens regardless of `interactive`** — silent refresh is exactly
     what lets the non-interactive watcher loop keep going.
   - refresh unavailable (no refresh token, past the 30-day cap, or `refresh_access_token`
     raised `AuthenticationRequired`) → fall back to the current behaviour: run the device flow
     if `interactive`, else raise `AuthenticationRequired`.
5. **New `teamgym-watcher logout` CLI command.** POST the cached refresh token to
   `{api}/device/logout` (best-effort — ignore the response beyond logging), then delete the
   token cache file. Idempotent and safe to run with no cached token. Add it alongside the
   `auth` command in `cli.py`.
6. **Verify the refreshed token reaches the API client.** `_build_deps` currently constructs
   `ApiClient(cfg.api_base_url, token)` once from a single `get_valid_token` call. Confirm how
   the watcher loop obtains a token per batch and ensure a silently-refreshed access token is
   actually used by subsequent requests (either the loop re-calls `get_valid_token` and rebuilds
   / updates the client, or the client is given a token-provider callback). If the current
   wiring would keep using a stale token after a refresh, fix it minimally and note the change
   in `docs/deviations.md`. Do **not** broaden this step into an API-client refactor.
7. **Docstrings.** Update `auth.py`'s module docstring and `get_valid_token`'s docstring to
   describe the new silent-refresh path and when `AuthenticationRequired` is still raised (cap
   reached, reuse detected, or de-authorized).

## Rules

- prompts/rules/python.md
- prompts/rules/testing.md
- prompts/rules/git.md

## Tests (`tests/test_auth.py`, pytest-httpx, injected `now`/`sleep`/`clock` — no real sleeps)

- Poll success now parses and caches `refreshToken` + `refreshTokenExpiresAt`; round-trips
  through `save`/`load`.
- Old-format cache (no refresh fields) still loads and behaves as "no refresh available".
- `get_valid_token`: fresh JWT → returned without any HTTP call.
- `get_valid_token`: expired JWT + valid refresh token → one `POST /device/refresh`, returns the
  new access token, and the **rotated** refresh token is persisted (assert the old one is gone).
- Silent refresh happens with `interactive=False` (no `AuthenticationRequired`, no device flow).
- `invalid_grant` on refresh → `AuthenticationRequired` and the dead token is cleared from cache.
- `access_denied` on refresh → `AuthenticationRequired` with the de-auth message.
- Past the 30-day cap (`refreshTokenExpiresAt` in the past): no refresh attempted;
  non-interactive raises `AuthenticationRequired`, interactive runs the device flow.
- Refresh network/parse failure surfaces as `AuthError`, not `AuthenticationRequired`.
- `logout`: POSTs the cached refresh token then removes the cache file; a second call with no
  cache is a no-op success (idempotent).

## Definition of done

`uv run ruff check . --fix && uv run ruff format .`, `uv run mypy src/`, `uv run pytest -q`
all clean; commit `feat: silent access-token refresh via refresh tokens`.
