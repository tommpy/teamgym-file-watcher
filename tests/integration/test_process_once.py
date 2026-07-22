"""End-to-end integration tests: ``process-once`` against a faked ``/tags`` API and a real
(tmp) filesystem, for all three competition types.

Marked ``integration`` (see ``pyproject.toml``) so a local ``uv run pytest -m "not integration"``
skips them -- each scenario does a real ``CliRunner`` invocation with a real (short) stability
wait, so this module is slower than the rest of the suite. CI runs the full suite (no marker
filter) on all three OSes, so these still execute there.

Three scenarios, one per ``CompetitionType``, each proving a different slice of the pipeline
end to end:

- International (:func:`test_international_scenario`): synthetic photos covering an
  exact tag-boundary match, a between-tags Random, a no-EXIF Random (with a journaled
  warning), and a RAW+JPEG companion pair landing together in an open-ended Awards tag --
  plus real multi-page ``/tags`` pagination (page size 2, 5 tags).
- Club (:func:`test_club_scenario`): the real Lightroom JPEGs from ``samples/`` proving
  real-world EXIF parses, the "ClubName - TeamName" composite segment, an Awards branch,
  and (:func:`test_club_scenario_second_run_collides_with_first`) a second run over the
  same ``dest_root`` with colliding filenames.
- InternationalClub (:func:`test_international_club_scenario`): the remaining real sample
  JPEGs, the Flat "Travelling" phase, and the "OlympicCode - ClubName" composite segment.

:func:`test_dry_run_leaves_filesystem_untouched` covers the ``--dry-run`` variant.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from teamgym_watcher.auth import AccessToken, save_token_cache
from teamgym_watcher.cli import app
from teamgym_watcher.journal import journal_path

from .helpers import (
    SAMPLES_DIR,
    award_json,
    group_json,
    list_tree,
    local_to_utc,
    make_jpeg,
    make_raw,
    paginated_tags_callback,
    read_journal,
    tag_json,
    team_json,
    utc,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.integration

runner = CliRunner()
API_BASE_URL = "http://api.test"
# A regex is required (rather than a plain string) so pytest-httpx matches regardless of the
# `page`/`limit` query parameters the client actually sends -- a plain string URL only matches
# requests with *no* query parameters at all.
TAGS_URL_PATTERN = re.compile(rf"^{re.escape(f'{API_BASE_URL}/tags')}(\?.*)?$")


def _authed_cache_path(tmp_path: Path, make_jwt: Callable[[dict[str, object]], str]) -> Path:
    cache_path = tmp_path / "token.json"
    jwt = make_jwt({"login": "octocat", "exp": 10_000_000_000})
    save_token_cache(cache_path, AccessToken(access_token=jwt, token_type="bearer", scope=""))
    return cache_path


def _invoke_process_once(
    monkeypatch: pytest.MonkeyPatch,
    cache_path: Path,
    import_dir: Path,
    dest_root: Path,
    *,
    dry_run: bool = False,
) -> object:
    monkeypatch.setattr("teamgym_watcher.cli.token_cache_path", lambda: cache_path)
    args = [
        "process-once",
        "--non-interactive",
        "--stability-seconds",
        "1",
        "--api-base-url",
        API_BASE_URL,
        "--import-dir",
        str(import_dir),
        "--dest-root",
        str(dest_root),
        "--timezone",
        "Europe/London",
    ]
    if dry_run:
        args.append("--dry-run")
    return runner.invoke(app, args)


def _mock_tags(httpx_mock: HTTPXMock, tags: list[dict[str, object]], *, page_size: int = 2) -> None:
    httpx_mock.add_callback(
        paginated_tags_callback(tags, page_size=page_size),
        url=TAGS_URL_PATTERN,
        method="GET",
        is_reusable=True,
    )


# --- International: exact-boundary / between-tags / no-EXIF / RAW+JPEG pair, + pagination ---

COMP_INTL_ID = 101
COMP_INTL_NAME = "Test International Open"


def _international_tags() -> list[dict[str, object]]:
    tags = [
        tag_json(
            id=1,
            competition_id=COMP_INTL_ID,
            competition_name=COMP_INTL_NAME,
            competition_type="INTERNATIONAL",
            start=utc(2026, 1, 10, 9, 0),
            end=utc(2026, 1, 10, 9, 10),
            section="WOMEN",
            age_group="JUNIOR",
            phase="FINALS",
            apparatus="TUMBLE",
            team=team_json(nationality="GBR"),
        ),
        tag_json(
            id=2,
            competition_id=COMP_INTL_ID,
            competition_name=COMP_INTL_NAME,
            competition_type="INTERNATIONAL",
            start=utc(2026, 1, 10, 9, 30),
            end=None,
            phase="AWARDS",
            award=award_json("Overall Champion"),
        ),
    ]
    # Filler tags, unrelated to any test photo, purely to push the tag count past one
    # page-size-2 page and exercise real multi-page pagination assembly.
    for n, day in enumerate((1, 2, 3), start=3):
        tags.append(
            tag_json(
                id=n,
                competition_id=COMP_INTL_ID,
                competition_name=COMP_INTL_NAME,
                competition_type="INTERNATIONAL",
                start=utc(2020, 1, day, 0, 0),
                end=utc(2020, 1, day, 1, 0),
                phase="TRAVELLING",
            )
        )
    return tags


def test_international_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    _mock_tags(httpx_mock, _international_tags(), page_size=2)

    make_jpeg(
        import_dir / "IMG_0001.JPG",
        date_time_original="2026:01:10 09:10:00",
        offset_time_original="+00:00",
    )
    make_jpeg(
        import_dir / "IMG_0002.JPG",
        date_time_original="2026:01:10 09:20:00",
        offset_time_original="+00:00",
    )
    make_jpeg(import_dir / "IMG_0003.JPG", date_time_original=None)
    make_jpeg(
        import_dir / "IMG_0004.JPG",
        date_time_original="2026:01:10 09:35:00",
        offset_time_original="+00:00",
    )
    make_raw(
        import_dir / "IMG_0004.CR2",
        date_time_original="2026:01:10 09:35:00",
        offset_time_original="+00:00",
    )
    original_boundary_bytes = (import_dir / "IMG_0001.JPG").read_bytes()
    original_raw_bytes = (import_dir / "IMG_0004.CR2").read_bytes()

    with caplog.at_level(logging.INFO):
        result = _invoke_process_once(monkeypatch, cache_path, import_dir, dest_root)

    assert result.exit_code == 0, result.output

    # Real multi-page pagination: 5 tags at page size 2 -> pages 1, 2, 3.
    tag_requests = httpx_mock.get_requests(url=TAGS_URL_PATTERN)
    assert len(tag_requests) == 3

    base = f"Competitions/{COMP_INTL_NAME}"
    assert list_tree(dest_root) == {
        f"{base}/Competition/Junior/Women/Great Britain/Tumble/IMG_0001.JPG",
        f"{base}/Random/IMG_0002.JPG",
        f"{base}/Random/IMG_0003.JPG",
        f"{base}/Awards/Overall Champion/IMG_0004.CR2",
        f"{base}/Awards/Overall Champion/IMG_0004.JPG",
    }

    # Moved files are byte-identical to the originals -- the watcher never modifies files.
    moved_boundary = dest_root / base / "Competition/Junior/Women/Great Britain/Tumble/IMG_0001.JPG"
    moved_raw = dest_root / base / "Awards/Overall Champion/IMG_0004.CR2"
    assert moved_boundary.read_bytes() == original_boundary_bytes
    assert moved_raw.read_bytes() == original_raw_bytes

    # Import dir left with nothing behind.
    assert list(import_dir.iterdir()) == []

    records = read_journal(journal_path(dest_root))
    assert len(records) == 4
    by_source = {Path(r["source_paths"][0]).name: r for r in records}
    assert by_source["IMG_0001.JPG"]["status"] == "moved"
    assert by_source["IMG_0001.JPG"]["tag_id"] == 1
    assert by_source["IMG_0002.JPG"]["status"] == "random"
    assert by_source["IMG_0002.JPG"]["tag_id"] is None
    assert by_source["IMG_0003.JPG"]["status"] == "random"
    assert by_source["IMG_0003.JPG"]["message"] == "Random: no capture time"
    assert by_source["IMG_0004.CR2"]["status"] == "moved"
    assert by_source["IMG_0004.CR2"]["tag_id"] == 2
    assert sorted(Path(p).name for p in by_source["IMG_0004.CR2"]["dest_paths"]) == [
        "IMG_0004.CR2",
        "IMG_0004.JPG",
    ]

    assert "No capture time for" in caplog.text
    assert "processed=4 matched=2 random=2 errors=0" in caplog.text


# --- Club: real Lightroom samples, Awards branch, "ClubName - TeamName" composite ---

COMP_CLUB_ID = 201
COMP_CLUB_NAME = "club-megatest-2026-07-20-19-01-50"


def _club_tags() -> list[dict[str, object]]:
    return [
        tag_json(
            id=10,
            competition_id=COMP_CLUB_ID,
            competition_name=COMP_CLUB_NAME,
            competition_type="CLUB",
            start=local_to_utc("2026-07-20T20:05:15"),
            end=local_to_utc("2026-07-20T20:08:00"),
            phase="AWARDS",
            award=award_json("Senior Division Champion"),
        ),
        tag_json(
            id=11,
            competition_id=COMP_CLUB_ID,
            competition_name=COMP_CLUB_NAME,
            competition_type="CLUB",
            start=local_to_utc("2026-07-20T20:03:00"),
            end=local_to_utc("2026-07-20T20:04:45"),
            phase="FINALS",
            apparatus="TUMBLE",
            group=group_json(name="Junior Mini", round_type="MINI"),
            team=team_json(club_name="Uppsala Athletics", team_name="Uppsala Youth"),
        ),
        tag_json(
            id=12,
            competition_id=COMP_CLUB_ID,
            competition_name=COMP_CLUB_NAME,
            competition_type="CLUB",
            start=utc(2026, 8, 1, 9, 0),
            end=utc(2026, 8, 1, 9, 10),
            phase="FINALS",
            apparatus="FLOOR",
            group=group_json(name="Senior A"),
            team=team_json(club_name="Riverside", team_name="Blue"),
        ),
        tag_json(
            id=13,
            competition_id=COMP_CLUB_ID,
            competition_name=COMP_CLUB_NAME,
            competition_type="CLUB",
            start=utc(2026, 8, 1, 9, 30),
            end=None,
            phase="AWARDS",
            award=award_json("Best Newcomer"),
        ),
    ]


CLUB_BASE = f"Competitions/{COMP_CLUB_NAME}"


def _populate_club_import_dir(import_dir: Path) -> dict[str, bytes]:
    """Real samples + the remaining synthetic edge cases (no-EXIF Random, RAW+JPEG pair).
    Returns original bytes of every file, keyed by filename, for later byte-identity checks."""
    originals: dict[str, bytes] = {}

    for filename in (
        "AWARD_Senior_Division_Champion_1784574391159.jpg",
        "Uppsala_Athletics_TUMBLE_1784574266783.jpg",
    ):
        shutil.copy2(SAMPLES_DIR / filename, import_dir / filename)
        originals[filename] = (SAMPLES_DIR / filename).read_bytes()

    make_jpeg(
        import_dir / "IMG_0010.JPG",
        date_time_original="2026:08:01 09:10:00",
        offset_time_original="+00:00",
    )
    make_jpeg(
        import_dir / "IMG_0011.JPG",
        date_time_original="2026:08:01 09:20:00",
        offset_time_original="+00:00",
    )
    make_jpeg(import_dir / "IMG_0012.JPG", date_time_original=None)
    make_jpeg(
        import_dir / "IMG_0013.JPG",
        date_time_original="2026:08:01 09:35:00",
        offset_time_original="+00:00",
    )
    make_raw(
        import_dir / "IMG_0013.CR2",
        date_time_original="2026:08:01 09:35:00",
        offset_time_original="+00:00",
    )

    for name in ("IMG_0010.JPG", "IMG_0011.JPG", "IMG_0013.JPG", "IMG_0013.CR2"):
        originals[name] = (import_dir / name).read_bytes()

    return originals


def test_club_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    _mock_tags(httpx_mock, _club_tags())
    originals = _populate_club_import_dir(import_dir)

    result = _invoke_process_once(monkeypatch, cache_path, import_dir, dest_root)

    assert result.exit_code == 0, result.output
    assert list_tree(dest_root) == {
        f"{CLUB_BASE}/Awards/Senior Division Champion/"
        "AWARD_Senior_Division_Champion_1784574391159.jpg",
        f"{CLUB_BASE}/Competition/Junior Mini/Uppsala Athletics - Uppsala Youth/Tumble/"
        "Uppsala_Athletics_TUMBLE_1784574266783.jpg",
        f"{CLUB_BASE}/Competition/Senior A/Riverside - Blue/Floor/IMG_0010.JPG",
        f"{CLUB_BASE}/Random/IMG_0011.JPG",
        f"{CLUB_BASE}/Random/IMG_0012.JPG",
        f"{CLUB_BASE}/Awards/Best Newcomer/IMG_0013.CR2",
        f"{CLUB_BASE}/Awards/Best Newcomer/IMG_0013.JPG",
    }
    assert list(import_dir.iterdir()) == []

    for filename, original_bytes in originals.items():
        moved = next(p for p in dest_root.rglob(filename) if p.is_file())
        assert moved.read_bytes() == original_bytes

    records = read_journal(journal_path(dest_root))
    assert len(records) == 6
    by_source = {Path(r["source_paths"][0]).name: r for r in records}
    assert by_source["AWARD_Senior_Division_Champion_1784574391159.jpg"]["tag_id"] == 10
    assert by_source["Uppsala_Athletics_TUMBLE_1784574266783.jpg"]["tag_id"] == 11
    assert by_source["IMG_0010.JPG"]["tag_id"] == 12
    assert by_source["IMG_0011.JPG"]["status"] == "random"
    assert by_source["IMG_0012.JPG"]["message"] == "Random: no capture time"
    assert by_source["IMG_0013.CR2"]["tag_id"] == 13


def test_club_scenario_second_run_collides_with_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    """A second ``process-once`` run over the same ``dest_root`` -- a fresh card imported into
    a fresh import directory, whose files collide by name (but not path; the journal dedups
    by full path, not filename, so a same-named but distinct photo is never silently dropped)
    with a matched and a Random destination folder -- must suffix rather than overwrite, and
    must append to (not replace) the journal."""
    first_import_dir = tmp_path / "import"
    second_import_dir = tmp_path / "import2"
    dest_root = tmp_path / "dest"
    first_import_dir.mkdir()
    second_import_dir.mkdir()
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    _mock_tags(httpx_mock, _club_tags())
    _populate_club_import_dir(first_import_dir)
    first = _invoke_process_once(monkeypatch, cache_path, first_import_dir, dest_root)
    assert first.exit_code == 0, first.output
    original_matched = (
        dest_root / CLUB_BASE / "Competition/Senior A/Riverside - Blue/Floor/IMG_0010.JPG"
    ).read_bytes()
    original_random = (dest_root / CLUB_BASE / "Random/IMG_0011.JPG").read_bytes()

    # A second card's fresh files, sharing a name with something already in dest_root.
    make_jpeg(
        second_import_dir / "IMG_0010.JPG",
        date_time_original="2026:08:01 09:05:00",
        offset_time_original="+00:00",
    )
    make_jpeg(
        second_import_dir / "IMG_0011.JPG",
        date_time_original="2026:08:01 09:25:00",
        offset_time_original="+00:00",
    )
    new_matched_bytes = (second_import_dir / "IMG_0010.JPG").read_bytes()
    new_random_bytes = (second_import_dir / "IMG_0011.JPG").read_bytes()

    second = _invoke_process_once(monkeypatch, cache_path, second_import_dir, dest_root)

    assert second.exit_code == 0, second.output
    matched_dir = dest_root / CLUB_BASE / "Competition/Senior A/Riverside - Blue/Floor"
    random_dir = dest_root / CLUB_BASE / "Random"

    # Originals untouched, no overwrite.
    assert (matched_dir / "IMG_0010.JPG").read_bytes() == original_matched
    assert (random_dir / "IMG_0011.JPG").read_bytes() == original_random
    # New files landed alongside with a collision suffix.
    assert (matched_dir / "IMG_0010-1.JPG").read_bytes() == new_matched_bytes
    assert (random_dir / "IMG_0011-1.JPG").read_bytes() == new_random_bytes

    records = read_journal(journal_path(dest_root))
    assert len(records) == 6 + 2  # first run's six groups, plus this run's two


# --- InternationalClub: real Lightroom samples, Flat phase, "OlympicCode - ClubName" ---

COMP_ICLUB_ID = 301
COMP_ICLUB_NAME = "megatest-2026-07-20-19-11-04"


def _international_club_tags() -> list[dict[str, object]]:
    return [
        tag_json(
            id=20,
            competition_id=COMP_ICLUB_ID,
            competition_name=COMP_ICLUB_NAME,
            competition_type="INTERNATIONAL_CLUB",
            start=local_to_utc("2026-07-20T20:10:00"),
            end=local_to_utc("2026-07-20T20:11:45"),
            phase="TRAVELLING",
        ),
        tag_json(
            id=21,
            competition_id=COMP_ICLUB_ID,
            competition_name=COMP_ICLUB_NAME,
            competition_type="INTERNATIONAL_CLUB",
            start=local_to_utc("2026-07-20T20:12:00"),
            end=local_to_utc("2026-07-20T20:13:00"),
            phase="PODIUM_TRAINING_WARMUP",
            apparatus="FLOOR",
            age_group="JUNIOR",
            section="WOMEN",
            group=group_json(name="Group 1"),
            team=team_json(club_name="Bohemian", nationality="CZE"),
        ),
    ]


def test_international_club_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
) -> None:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    _mock_tags(httpx_mock, _international_club_tags())

    originals: dict[str, bytes] = {}
    for filename in (
        "GENERAL_TRAVELLING_1784574693256.jpg",
        "Bohemian_FLOOR_1784574758672.jpg",
    ):
        shutil.copy2(SAMPLES_DIR / filename, import_dir / filename)
        originals[filename] = (SAMPLES_DIR / filename).read_bytes()
    make_jpeg(import_dir / "IMG_0020.JPG", date_time_original="2026:07:20 20:20:00")

    result = _invoke_process_once(monkeypatch, cache_path, import_dir, dest_root)

    assert result.exit_code == 0, result.output
    base = f"Competitions/{COMP_ICLUB_NAME}"
    assert list_tree(dest_root) == {
        f"{base}/Travelling/GENERAL_TRAVELLING_1784574693256.jpg",
        f"{base}/PodiumTrainingWarmup/Junior/Women/CZE - Bohemian/Floor/"
        "Bohemian_FLOOR_1784574758672.jpg",
        f"{base}/Random/IMG_0020.JPG",
    }
    assert list(import_dir.iterdir()) == []

    for filename, original_bytes in originals.items():
        moved = next(p for p in dest_root.rglob(filename) if p.is_file())
        assert moved.read_bytes() == original_bytes

    records = read_journal(journal_path(dest_root))
    assert len(records) == 3
    by_source = {Path(r["source_paths"][0]).name: r for r in records}
    assert by_source["GENERAL_TRAVELLING_1784574693256.jpg"]["tag_id"] == 20
    assert by_source["Bohemian_FLOOR_1784574758672.jpg"]["tag_id"] == 21
    assert by_source["IMG_0020.JPG"]["status"] == "random"


# --- Dry-run: identical logging expectations, filesystem entirely untouched ---

COMP_DRY_ID = 401
COMP_DRY_NAME = "Dry Run Open"


def test_dry_run_leaves_filesystem_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_jwt: Callable[[dict[str, object]], str],
    httpx_mock: HTTPXMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import_dir = tmp_path / "import"
    dest_root = tmp_path / "dest"
    import_dir.mkdir()
    cache_path = _authed_cache_path(tmp_path, make_jwt)
    tags = [
        tag_json(
            id=1,
            competition_id=COMP_DRY_ID,
            competition_name=COMP_DRY_NAME,
            competition_type="INTERNATIONAL",
            start=utc(2026, 3, 1, 9, 0),
            end=utc(2026, 3, 1, 9, 10),
            section="MIXED",
            age_group="SENIOR",
            phase="FINALS",
            apparatus="FLOOR",
            team=team_json(nationality="SWE"),
        )
    ]
    _mock_tags(httpx_mock, tags)
    make_jpeg(
        import_dir / "matched.JPG",
        date_time_original="2026:03:01 09:05:00",
        offset_time_original="+00:00",
    )
    make_jpeg(
        import_dir / "random.JPG",
        date_time_original="2026:03:01 09:20:00",
        offset_time_original="+00:00",
    )
    matched_bytes = (import_dir / "matched.JPG").read_bytes()
    random_bytes = (import_dir / "random.JPG").read_bytes()

    with caplog.at_level(logging.INFO):
        result = _invoke_process_once(monkeypatch, cache_path, import_dir, dest_root, dry_run=True)

    assert result.exit_code == 0, result.output
    assert "processed=2 matched=1 random=1 errors=0" in caplog.text

    # Nothing moved: sources untouched, no Competitions/ tree created.
    assert (import_dir / "matched.JPG").read_bytes() == matched_bytes
    assert (import_dir / "random.JPG").read_bytes() == random_bytes
    assert not (dest_root / "Competitions").exists()

    records = read_journal(journal_path(dest_root))
    assert len(records) == 2
    by_source = {Path(r["source_paths"][0]).name: r for r in records}
    assert by_source["matched.JPG"]["status"] == "moved"
    assert by_source["matched.JPG"]["tag_id"] == 1
    assert by_source["random.JPG"]["status"] == "random"
