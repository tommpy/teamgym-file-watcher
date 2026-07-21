# Backlog

Known gaps, deliberately out of scope for the initial build (see `PLAN.md`'s Risks section).
Not tracked as in-code `TODO`s so they don't rot silently — recorded here instead.

## Sorter-API refresh tokens for long unattended runs

JWTs issued by the device flow last one hour and the API has no refresh-token flow
(`auth.py`'s `EARLY_EXPIRY_BUFFER_SECONDS` docstring, `PLAN.md`'s "Auth" section). A `run`
process left unattended past that pauses its processing queue and logs a re-auth prompt every
60 seconds until `teamgym-watcher auth` is run again — no photos are dropped, but a
genuinely unattended competition (multi-hour, no one at the laptop) needs someone to
re-authenticate at least once an hour. Fixing this requires a refresh-token flow on the
teamgym-sorter API side; out of scope for this repo alone.

## Direct Zenfolio upload

The watcher currently only moves photos into a folder structure; getting them onto Zenfolio
still requires a separate upload step (manual, or a third-party sync/uploader pointed at
`dest_root` — see the README's "Zenfolio upload" section). A built-in uploader (Zenfolio API
integration, incremental/watch-based upload of newly-moved files) would close that last gap,
but adds a second network dependency and its own auth/retry/rate-limit surface — deferred
until the manual/synced workflow proves insufficient in practice.
