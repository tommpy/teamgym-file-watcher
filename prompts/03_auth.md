# Step 03 — GitHub device-flow authentication

Read `PLAN.md`. Port the behaviour of
`../lightroom-plugins/teamgym.lrdevplugin/ApiAuthentication.lua` (read it fully) and the
auth section of `../teamgym-sorter/docs/api/README.md`.

## Goal
`auth.py`: device-flow login against the sorter API with cached JWT, matching the plugin's
behaviour.

## Requirements
1. Flow (RFC 8628, via the sorter API — not GitHub directly):
   `POST {api}/device/start` → `{userCode, verificationUriComplete, deviceCode, interval,
   expiresIn}`; display code + URL prominently (Rich panel + plain-text fallback);
   poll `POST {api}/device/poll` with `{deviceCode}` respecting `interval` (min 5 s, matching
   `ApiConfig.AUTH.MIN_POLLING_INTERVAL`) until JWT, denial, or expiry (max 15 min).
2. Token cache: JSON at `platformdirs.user_config_dir("teamgym-watcher")/token.json`,
   permissions 0600 where supported. Decode the JWT payload (base64url, no signature
   verification — same as the plugin) to get `exp`; treat as expired 5 minutes early
   (plugin's buffer).
3. `get_valid_token(config, *, interactive: bool) -> str`:
   cached-and-fresh → return; expired + interactive → run device flow; expired +
   non-interactive → raise `AuthenticationRequired`.
4. The watcher loop (later steps) will catch `AuthenticationRequired`, pause the queue, and
   log the re-auth instruction loudly every 60 s — note this contract in the docstring.
5. `teamgym-watcher auth` command: force a fresh device flow, save, print identity claims
   (login/email) from the JWT payload.
6. Tests (pytest-httpx, injected clock/sleep): happy path, `authorization_pending` then
   success, denial, device-code expiry, cache round-trip, early-expiry buffer,
   non-interactive raise. No real sleeps.

## Definition of done
Checks pass; commit `feat: GitHub device-flow auth with cached JWT`.
