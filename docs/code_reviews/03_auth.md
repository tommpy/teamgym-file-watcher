# Code review — Step 03: GitHub device-flow auth (`d3ffedc`)

Reviewed against `prompts/03_auth.md`, `ApiAuthentication.lua`, `ApiConfig.lua`, and
`../teamgym-sorter/docs/api/{README.md,main-api.yaml}`.

## Must-fix (found and fixed)

**`_start_device_flow` / `_poll_device_authorization` called `response.raise_for_status()`
before parsing the body.** Per `main-api.yaml`, `/device/start` returns its
`{success, error, message}` error shape on HTTP 400 and 500 (not just 200), and
`/device/poll` returns 400 for an invalid device code. `raise_for_status()` turned those
into an uncaught `httpx.HTTPStatusError` instead of a clean `AuthError` with the server's
message — the CLI would crash with a stack trace instead of exiting cleanly via
`AUTH_ERROR_EXIT_CODE`. It also diverged from the plugin: `ApiAuthentication.lua`'s
`makeHttpRequest` (via `LrHttp`) never inspects the HTTP status code and just parses
whatever JSON body comes back, treating `success: false` as the error path regardless of
status.

The existing `test_start_failure_raises_auth_error` mocked the failure body at HTTP 200,
which doesn't match the documented 400/500 failure responses, so it didn't catch this.

Fix: added `_parse_response_body()`, used by both call sites, which parses the JSON body
(raising `AuthError` on invalid/non-object JSON) without checking status code first. Added
`test_start_failure_with_500_status_raises_auth_error` and
`test_poll_failure_with_400_status_raises_auth_error` to cover the real failure shapes.

## Checked, no issues

- **Device flow semantics**: start → display (Rich panel + logged plain text) → poll,
  `interval` floored at `MIN_POLLING_INTERVAL_SECONDS` (5s, matches
  `ApiConfig.AUTH.MIN_POLLING_INTERVAL`), `slow_down` adds 2s (matches the plugin's
  `pollingInterval + 2`), `authorization_pending`/`access_denied`/`expired_token` handled
  per the plugin's `pollDeviceAuthorization`. Deadline uses injected monotonic `clock()`
  against `expires_in` rather than the plugin's attempt-count approximation — equivalent
  behaviour, more precise under `slow_down`.
- **Token cache**: JSON at `platformdirs.user_config_dir(...)/token.json`, `chmod 0600`
  best-effort (wrapped in `contextlib.suppress(OSError)`, correctly a no-op on Windows).
  Round-trip, missing-file, and malformed-file cases covered.
- **Expiry buffer**: `EARLY_EXPIRY_BUFFER_SECONDS = 300`, comparison
  `(now + buffer) >= exp`, matches `ApiAuthentication.lua`'s `isTokenExpired` exactly,
  including "no `exp` claim → never expires."
- **`get_valid_token` contract**: cached+fresh → return; interactive+stale → device flow;
  non-interactive+stale → `AuthenticationRequired`. Matches the documented contract for the
  later watcher loop.
- **File immutability / photo safety**: not applicable — this step touches no photo I/O.
- **Cross-platform**: `pathlib` throughout, no shell-outs, permission-restriction failure
  is swallowed rather than fatal.
- **Tests**: no real sleeps (`FakeClock` injected via `sleep`/`clock`), no real network
  (`pytest_httpx`), meaningful assertions on sleep sequencing and error types.
- ruff, mypy (`--strict`-equivalent config), pytest all clean after the fix
  (65 passed, 1 skipped — the Windows-skipped POSIX permissions test).

## Minor, not fixed

`ApiAuthentication.lua`'s `parseJson` strips embedded whitespace from `accessToken`
(`string.gsub(token, "%s+", "")`) before storing it; the Python port doesn't. No evidence
in the API docs that tokens ever contain embedded whitespace, and it isn't called out in
`prompts/03_auth.md`'s requirements, so left as-is rather than adding speculative
sanitisation.
