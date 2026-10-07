# Step 17 — Live terminal status dashboard

Read `PLAN.md`, `src/teamgym_watcher/cli.py`, `log.py`, `pipeline.py` (`format_summary_line`,
`PipelineDeps`), `journal.py` (`Stats`/`StatsSnapshot`), and `watcher.py` (`Watcher.queue`).

## Why

Today `run`'s only feedback is scrolling `RichHandler` log lines plus a one-line
`processed=… matched=… random=… errors=… queued=…` summary logged every 60s
(`cli.py::_run_watch_loop`), and a `Panel` printed on its own `Console()` when auth is
required (`_log_auth_required`). For a multi-hour unattended import session an operator
watching the terminal has no at-a-glance, continuously-current view of queue depth or counts
between those 60-second ticks, and no persistent "am I authenticated right now" indicator.
This step adds a live, always-current status view — still pure terminal output (no GUI, no
new window), and still fully scriptable: piped/redirected/CI/test invocations must see
**exactly** today's plain log output, unchanged.

## Library choice (decided here so it isn't re-litigated mid-implementation)

Considered: Rich `Live`/`Layout` (already a dependency via `rich>=13.7`), **Textual** (same
authors, full TUI framework), and `urwid`/`blessed`/`python-prompt-toolkit`.

**Decision: Rich `Live`, sharing the existing `RichHandler`'s `Console`.** Reasons:

- Zero new dependency — `rich` is already pinned for logging.
- Rich's documented coexistence pattern is exactly what's needed: ordinary
  `console.print`/logging output interleaves correctly *above* an active `Live` region when
  they share one `Console` instance, so the existing scrolling per-photo log lines keep working
  unchanged and the dashboard is purely an additional, faster-updating footer.
- The watch loop is synchronous (`threading` + `signal.signal` in `cli.py::_run_watch_loop`);
  `Live` slots into that directly. Textual requires its own async app loop, which would mean
  rebuilding the Ctrl-C handling and the watcher's threading model for no functional gain here.
- `Live`'s renderable can be built by a pure function and tested by rendering to a
  `Console(record=True)` / `io.StringIO()` — no real terminal required, no flaky screen-capture
  tests.

Textual (or genuine keyboard interactivity — pause/resume, filtering, scrolling history) is a
reasonable future step if ever wanted, but is out of scope here; if it comes up, file it in
`docs/backlog.md` rather than building it speculatively now.

## Requirements

1. **New module `src/teamgym_watcher/dashboard.py`**:
   - `ActivityEvent` (frozen dataclass): `timestamp: datetime`, `filename: str`,
     `status: JournalStatus`, `detail: str`. (Reserved for the recent-activity row if you find
     room for one in the layout; not required to be wired up anywhere yet if it doesn't fit —
     don't force it in.)
   - `DashboardState` (frozen dataclass): `started_at: datetime`, `now: datetime`,
     `mode: str` (`"run"` or `"process-once"`), `dry_run: bool`, `import_dir: Path`,
     `dest_root: Path`, `api_base_url: str`, `processed: int`, `matched: int`, `random: int`,
     `errors: int`, `queued: int`, `auth_ok: bool`, `auth_message: str | None`.
   - `render_dashboard(state: DashboardState) -> RenderableType` — a **pure function**
     returning a Rich renderable (e.g. a `Group` of `Panel`/`Table`): a header line (mode,
     dry-run badge, elapsed since `started_at`), a stats line/table matching
     `format_summary_line`'s fields, and an auth status line — green "Authenticated" when
     `auth_ok`, otherwise the same wording `_log_auth_required` uses today so the message
     doesn't fork into two different phrasings of the same fact.
   - `should_use_dashboard(console: Console, ui_mode: str) -> bool`: `"dashboard"` → `True`,
     `"plain"` → `False`, `"auto"` (default) → `console.is_terminal and not console.is_dumb_terminal`.
   - `class LiveDashboard`: thin wrapper around `rich.live.Live(console=..., transient=False)`
     constructed with an **explicit, caller-supplied `Console`** (no default `Console()` of its
     own — the whole point is one shared console with the logger). `start()`/`stop()` (or
     context-manager support) and `update(state: DashboardState) -> None`, which calls
     `self._live.update(render_dashboard(state))`.

2. **`log.py::setup_logging` gains `console: Console | None = None`.** When given, the
   `RichHandler` is constructed with that exact instance instead of creating its own; when
   omitted, behaviour is pixel-for-pixel what it is today (both existing call sites, and
   `tests/test_log.py`, must keep passing unmodified).

3. **New `--ui [auto|dashboard|plain]` option** (default `auto`) on `run` and `process-once`
   only (not `auth`/`logout` — there's no loop to attach a dashboard to).

4. **Wire-up in `cli.py`**:
   - Build one `Console()`, pass it to `setup_logging(..., console=console)`.
   - Compute `use_dashboard = should_use_dashboard(console, ui_mode)`; construct a
     `LiveDashboard(console)` when true, else proceed with `dashboard=None`.
   - Give `_run_watch_loop` and `_drain_once` a new `dashboard: LiveDashboard | None = None`
     parameter (default `None` so every existing direct unit test of these two functions keeps
     working untouched). When present, call `dashboard.update(...)` with a freshly built
     `DashboardState` on every poll tick in `_run_watch_loop` (same cadence as
     `poll_interval_seconds`) and after every processed chunk in `_drain_once` — reuse the
     existing loop timing; do not add a second timer/thread.
   - `_log_auth_required` must print through the **same shared `Console`** passed in (not
     construct its own) — a second independent `Console` writing to stdout while a `Live` owns
     the region will corrupt the terminal. When a dashboard is active, its status line already
     carries the auth-required state every tick; the periodic `Panel` print and
     `logger.warning` continue as today for the plain/file-log record.
   - Start the `Live` region only after `_build_deps`'s auth check succeeds and the watcher has
     started, and always stop it in the same `finally` that restores the signal handler and
     stops the watcher — Ctrl-C or any exception must leave the terminal in a clean state, never
     a half-drawn live region.

5. **Plain-mode output must be byte-for-byte unchanged.** Any invocation where
   `should_use_dashboard` resolves `False` (explicit `--ui plain`, or `--ui auto` against a
   non-tty — which is every `CliRunner` invocation, CI, and piped/redirected real usage) must
   produce **exactly** today's log output. This is the core backward-compatibility guarantee;
   treat any diff here as a bug.

6. **`process-once`** shows the dashboard for the duration of the drain and leaves the final
   frame on screen (`transient=False`) when it exits; the existing final
   `logger.info(format_summary_line(...))` call is unaffected.

## Rules

- prompts/rules/python.md
- prompts/rules/testing.md
- prompts/rules/git.md

## Tests

- `tests/test_dashboard.py`:
  - `render_dashboard`: build a `DashboardState`, render via
    `Console(record=True, width=100, force_terminal=True)`, assert `export_text()` contains the
    expected counts, mode label, dry-run badge, and both the authenticated and
    auth-required phrasings (one assertion per state, not both at once).
  - `should_use_dashboard`: parametrised over `(is_terminal, is_dumb_terminal, ui_mode)` →
    expected bool, covering `auto` on a real tty, `auto` piped, `dashboard` forced on a
    non-tty, and `plain` forced on a tty.
  - `LiveDashboard`: construct against `Console(file=io.StringIO(), force_terminal=True)`;
    call `update()` twice with different states; assert the captured output reflects the
    latest state.
- `tests/test_log.py`: add a test that `setup_logging(console=some_console)` attaches a
  `RichHandler` using that exact instance; confirm the four pre-existing tests still pass with
  no `console=` kwarg.
- `tests/test_cli.py`:
  - Existing direct calls to `_run_watch_loop`/`_drain_once` (no `dashboard` argument) keep
    passing unmodified.
  - New direct-call test(s) passing a stub object exposing `update()` and asserting it's
    called at least once per tick/batch.
  - A `CliRunner` invocation of `run`/`process-once` with `--ui auto` (default) produces
    identical output to the pre-existing assertions — regression guard that non-tty
    auto-detection falls back to plain.
  - A `CliRunner` invocation with `--ui dashboard` forced against the runner's captured
    (non-tty) stream still completes without error (constructing/using `Live` must not require
    a real tty), even though nothing visually-live is asserted beyond "it didn't crash".

## Definition of done

`uv run ruff check . --fix && uv run ruff format .`, `uv run mypy src/`, `uv run pytest -q`
all clean. Add a short "Live dashboard" subsection to `README.md` documenting `--ui` and what
it looks like. Commit `feat: live terminal status dashboard`.
