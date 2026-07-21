# teamgym-file-watcher

Watches an import directory for TeamGym competition photos, matches each one to a
competition tag from the [teamgym-sorter](../teamgym-sorter) API by capture time, and
physically moves it into a Zenfolio-ready folder hierarchy on disk — so photos are ready to
upload the moment a card is ingested, without waiting on a Lightroom export.

Files are **never modified**. The API remains the single source of truth for tag metadata;
when the moved files are later imported into Lightroom, the existing
[`teamgym.lrdevplugin`](../lightroom-plugins/teamgym.lrdevplugin) plugin re-tags them from
the API exactly as it does today, and its collection builder produces collections that match
the on-disk folders the watcher chose — both derive from the same hierarchy logic. See
[`PLAN.md`](PLAN.md) for the full design and rationale.

## Quick start

Requires Python ≥ 3.12.

```bash
# from a clone, with uv (https://docs.astral.sh/uv/)
uv sync
uv run teamgym-watcher --help

# or install as a standalone CLI
pipx install .
teamgym-watcher --help
```

1. Copy the example config and fill in your competition's details:

   ```bash
   cp teamgym-watcher.example.toml teamgym-watcher.toml
   ```

   At minimum, set `api_base_url`, `competition_id`, `import_dir` (must already exist), and
   `dest_root`. See the [config reference](#config-reference) below for every setting.

2. Authenticate once (see [Auth](#auth) below):

   ```bash
   uv run teamgym-watcher auth --config teamgym-watcher.toml
   ```

3. Do a dry run first to see what would happen without touching any files:

   ```bash
   uv run teamgym-watcher run --config teamgym-watcher.toml --dry-run
   ```

4. Watch for real:

   ```bash
   uv run teamgym-watcher run --config teamgym-watcher.toml
   ```

   Or drain whatever's currently in the import directory once and exit (useful for catch-up,
   or for re-running after `teamgym-watcher` was offline):

   ```bash
   uv run teamgym-watcher process-once --config teamgym-watcher.toml
   ```

Every setting in the TOML file can also be overridden per-invocation with the matching CLI
option, e.g. `--competition-id 42`. Run `teamgym-watcher run --help` for the full list.

## Config reference

Settings live in a TOML file (default `teamgym-watcher.toml` in the current directory, or
pass `--config /path/to/file.toml`). See
[`teamgym-watcher.example.toml`](teamgym-watcher.example.toml) for a filled-in template with
inline comments.

| Setting | Type | Default | CLI override | Description |
|---|---|---|---|---|
| `api_base_url` | string | *required* | `--api-base-url` | Base URL of the teamgym-sorter API. |
| `competition_id` | integer | *required* | `--competition-id` | Numeric id of the competition to fetch tags for. |
| `import_dir` | path | *required* | `--import-dir` | Directory the watcher monitors for new photos. Must already exist. |
| `dest_root` | path | *required* | `--dest-root` | Root directory photos are moved into, under `<dest_root>/Competitions/<Competition>/...`. Created automatically. |
| `timezone` | string \| unset | machine's local timezone | `--timezone` | IANA timezone name (e.g. `Europe/London`) used when a photo has no embedded UTC offset. |
| `time_offset_seconds` | integer | `0` | `--time-offset-seconds` | Added to (or subtracted from, if negative) each photo's capture time — corrects camera clock skew relative to the server. |
| `tag_refresh_seconds` | integer | `60` | `--tag-refresh-seconds` | How often to re-fetch the full tag list for the competition from the API. |
| `stability_seconds` | integer | `3` | `--stability-seconds` | How long a file's size must be unchanged before it's considered fully written and safe to process. |
| `batch_size` | integer | `25` | `--batch-size` | How many stable files to drain from the watch queue per processing batch. |
| `dry_run` | boolean | `false` | `--dry-run` / `--no-dry-run` | When true, log every detection/match/move but never actually move files. |
| `recursive` | boolean | `false` | — (TOML only) | When true, also watch subdirectories of `import_dir`, not just its top level. |
| `exiftool_enabled` | boolean | `false` | — (TOML only) | When true, fall back to an external `exiftool` process for RAW formats `exifread` cannot parse (currently just `.cr3`). |
| `exiftool_path` | string | `"exiftool"` | — (TOML only) | Path to the `exiftool` binary, used only when `exiftool_enabled` is true. |

`import_dir` and `dest_root` must be different directories, and neither may be nested inside
the other.

Other CLI-only flags (all commands): `--config`, `--verbose`/`-v`. `run` and `process-once`
also take `--non-interactive` (never prompt for re-authentication; pause and log instead) and
`--log-file` (also write logs to a rotating file).

## Auth

The CLI uses the same GitHub OAuth [device flow](https://datatracker.ietf.org/doc/html/rfc8628)
as the Lightroom plugin:

```bash
uv run teamgym-watcher auth --config teamgym-watcher.toml
```

This prints a one-time code and a URL (e.g. `https://github.com/login/device`). Open the URL,
enter the code, and authorize. The resulting JWT is cached under the platform config
directory (`token.json`) and reused by `run`/`process-once` as long as it's cached and not
within five minutes of expiring. Each fresh invocation of `run`/`process-once` re-runs the
device flow interactively if the cached token has expired — unless `--non-interactive` is
set, in which case it fails fast instead of prompting.

Once a `run` process is already up and watching, a token expiring *mid-run* is a separate
case: the process itself never prompts interactively (there may be no one watching the
terminal). It pauses the processing queue, logs a re-auth prompt every 60 seconds, and
resumes automatically once you run `teamgym-watcher auth` in another terminal to refresh the
cached token — no queued photos are dropped while it waits (see
[Troubleshooting](#troubleshooting)).

## Hierarchy examples

Photos are moved to `<dest_root>/Competitions/<Competition>/...`, where the rest of the path
depends on the matched tag's competition type and phase — ported exactly from the plugin's
`PhotoCollectionHierarchy.lua` so the folders match the collections Lightroom builds later.

**International** (nested phases: `Phase / Age / Section / Nationality / Apparatus`):

```
Competitions/European Championships 2026/Competition/Senior/Women/Great Britain/Floor/IMG_1234.CR2
```

**Club** (nested phases: `Phase / Group / "ClubName - TeamName" / Apparatus`):

```
Competitions/Club Championships 2026/Qualification/Group 1/Riverside Gymnastics - Riverside Senior Women/Tumble/IMG_5678.jpg
```

**InternationalClub** (nested phases: `Phase / Age / Section / "OlympicCode - ClubName" / Apparatus`):

```
Competitions/European Club Cup 2026/Competition/Senior/Mixed/GBR - Riverside Gymnastics/Trampet/IMG_9012.jpg
```

Some phases don't nest this deeply:

- **Flat** phases (`Travelling`, `Banquet`, `OpeningCeremony`) stop at `Phase` (plus `Group`
  for Club): `Competitions/<Competition>/Travelling/IMG_0001.jpg`.
- **Awards** always goes to `Competitions/<Competition>/Awards/<AwardsSection>/IMG_0002.jpg`
  regardless of competition type.
- A photo that doesn't match any tag (or whose EXIF has no usable capture time) — or any
  missing level of a matched tag's metadata — falls back to a `Random` folder at the first
  missing level, e.g. `Competitions/<Competition>/Random/IMG_9999.jpg`. These photos are never
  dropped; they're just easy to spot and re-sort by hand.

RAW+JPEG pairs (and any sidecar sharing a basename) always move together as a unit, so they
land in the same folder.

## Zenfolio upload

Because photos land in their final folder structure as soon as they're ingested, `dest_root`
can be pointed at (or synced to) whatever directory your Zenfolio uploader watches — no need
to wait for a full Lightroom export before uploads start. `dest_root`'s
`Competitions/<Competition>/...` layout is stable and predictable, so it's safe to point an
uploader at it while the watcher is still running; already-moved files are never touched
again.

## Lightroom workflow

The watcher only sorts files on disk — it never writes metadata into them. The existing
Lightroom plugin workflow is unchanged:

1. Import the moved files from `dest_root` into Lightroom as normal.
2. Run the plugin's `BulkPhotoTagger` to tag the imported photos from the same
   teamgym-sorter API, exactly as before.
3. Run the plugin's collection builder — its collection paths will match the on-disk folder
   paths the watcher chose for the same photos, because both are driven by the same
   phase-based hierarchy rules.

See [`docs/verification.md`](docs/verification.md) for a manual checklist to confirm this
parity after setup changes.

## Troubleshooting

**"Authentication required" / token expiry.** JWTs from the API last one hour and there is no
refresh flow (see [`docs/backlog.md`](docs/backlog.md)), so a genuinely unattended `run` longer
than that will hit this mid-run. Neither command drops queued photos when this happens:
`run` pauses processing, logs a re-auth prompt every 60 seconds, and picks the queue back up
automatically as soon as `teamgym-watcher auth` (run in another terminal) refreshes the
cached token. `process-once` instead exits with an error and leaves the not-yet-processed
files untouched in the import directory — re-run it after authenticating.

**Unsupported RAW format (e.g. `.cr3`).** `exifread` parses JPEG and TIFF-based RAW formats
(NEF, CR2, ARW, DNG, ...) but not Canon's CR3 container. If you shoot CR3, install
[exiftool](https://exiftool.org/) and set `exiftool_enabled = true` (and `exiftool_path` if
it's not on `PATH`) in your config. Without this, CR3 files raise a startup error rather than
silently mis-sorting. `EXIFTOOL_ONLY_EXTENSIONS` in `src/teamgym_watcher/exif.py` is the
authoritative list if another such format turns up.

**Photos landing in `Random` when you expected a match.** Every photo's outcome is logged at
info level, naming why it didn't fully match: no usable capture time, or no tag covering that
capture time (check `time_offset_seconds` for camera clock skew, since the photo's own clock
is trusted as-is otherwise). If a tag *did* match but the folder still bottoms out at
`Random` partway down, the tag itself is missing a field the hierarchy needs (e.g. no
`ClubName`, or an unmapped nationality code) — mapping logs a warning naming the specific
field whenever that happens.

**Timezone diagnostics.** Run with `--verbose` to see, per photo, which timezone source was
used: the photo's own embedded `OffsetTimeOriginal`, the configured `timezone` fallback, or
the machine's local timezone (in that priority order). If capture times look shifted, check
the camera's clock and timezone setting first — the watcher trusts the photo's own offset
above everything else when it's present.

## Windows & macOS specifics

- **Long paths (Windows).** Deeply nested competitions (long competition/team/apparatus
  names) can approach Windows' historical 260-character path limit. Enable
  [long path support](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation#enable-long-paths-in-windows-10-version-1607-and-later)
  (Windows 10 1607+, via Group Policy or registry) if you hit `OSError` on deeply nested
  moves.
- **Running as a background service.** `teamgym-watcher run` is a long-lived foreground
  process; wrap it with your platform's usual service mechanism if you want it to survive
  logout/reboot — [NSSM](https://nssm.cc/) or Task Scheduler on Windows, `launchd` (a user
  `LaunchAgent` plist running `teamgym-watcher run --config ... --log-file ...`) on macOS.
- **Case-insensitive filesystems.** Both platforms' default filesystems are
  case-insensitive; companion-file grouping and collision-safe moves are written to be safe
  under that (e.g. `IMG_0001.CR2` and `img_0001.jpg` are grouped as a pair, not treated as
  unrelated files).

## Repo layout

```
src/teamgym_watcher/   Application source (see PLAN.md's Architecture section for a
                       per-module breakdown)
tests/                 pytest suite, including tests/integration/ end-to-end coverage
samples/               Real Lightroom-written JPEGs used as EXIF extraction fixtures
docs/                  building.md, verification.md, deviations.md, backlog.md
teamgym-watcher.example.toml   Copy this to get started
PLAN.md                Full design doc
```

For how this repo itself was built (an automated multi-step agentic pipeline), see
[`docs/building.md`](docs/building.md).
