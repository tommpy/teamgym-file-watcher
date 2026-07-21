"""Tests for the Tags API client: pagination, retry, defensive parsing. No real network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from teamgym_watcher.api import (
    ApiClient,
    ApiError,
    Award,
    Competition,
    Group,
    Tag,
    TagParseError,
    Team,
    Unauthorized,
    parse_tag,
)

API_BASE_URL = "http://api.test"


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _tag_json(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "startTime": "2024-10-05T14:30:00.000Z",
        "endTime": "2024-10-05T15:45:00.000Z",
        "section": "WOMEN",
        "ageGroup": "SENIOR",
        "phase": "FINALS",
        "apparatus": "FLOOR",
        "competition": {"id": 123, "name": "World Champs", "type": "INTERNATIONAL"},
        "team": {
            "id": 456,
            "clubName": "Stockholm Gymnastics",
            "teamName": None,
            "nationality": "SWE",
            "clubId": None,
        },
        "award": None,
        "group": None,
    }
    base.update(overrides)
    return base


class TestParseTag:
    def test_parses_full_tag(self) -> None:
        tag = parse_tag(
            _tag_json(
                award={"id": 10, "name": "Best Performance"},
                group={"id": 2, "name": "Group A", "roundType": "FULL"},
            )
        )

        assert tag == Tag(
            id=1,
            competition_id=123,
            start_time=datetime(2024, 10, 5, 14, 30, 0, tzinfo=UTC),
            end_time=datetime(2024, 10, 5, 15, 45, 0, tzinfo=UTC),
            section="WOMEN",
            age_group="SENIOR",
            phase="FINALS",
            apparatus="FLOOR",
            team=Team(
                id=456,
                club_name="Stockholm Gymnastics",
                team_name=None,
                nationality="SWE",
                club_id=None,
            ),
            group=Group(name="Group A", round_type="FULL"),
            award=Award(name="Best Performance"),
            competition=Competition(name="World Champs", type="INTERNATIONAL"),
        )

    def test_naive_start_time_is_treated_as_utc(self) -> None:
        tag = parse_tag(_tag_json(startTime="2024-10-05T14:30:00"))

        assert tag.start_time == datetime(2024, 10, 5, 14, 30, 0, tzinfo=UTC)

    def test_open_tag_has_none_end_time(self) -> None:
        tag = parse_tag(_tag_json(endTime=None))

        assert tag.end_time is None

    def test_null_team_group_award_are_none(self) -> None:
        tag = parse_tag(_tag_json(team=None, group=None, award=None))

        assert tag.team is None
        assert tag.group is None
        assert tag.award is None

    def test_event_only_tag_has_null_section_age_apparatus(self) -> None:
        tag = parse_tag(
            _tag_json(section=None, ageGroup=None, apparatus=None, team=None, phase="TRAVELLING")
        )

        assert tag.section is None
        assert tag.age_group is None
        assert tag.apparatus is None
        assert tag.team is None

    def test_club_competition_team_uses_team_name_and_club_id(self) -> None:
        tag = parse_tag(
            _tag_json(
                section=None,
                ageGroup=None,
                team={
                    "id": 20,
                    "clubName": "Stockholm Gymnastics",
                    "nationality": None,
                    "teamName": "Stockholm Stars A",
                    "clubId": 5,
                },
            )
        )

        assert tag.team == Team(
            id=20,
            club_name="Stockholm Gymnastics",
            team_name="Stockholm Stars A",
            nationality=None,
            club_id=5,
        )

    def test_team_missing_own_fields_defaults_to_none(self) -> None:
        tag = parse_tag(_tag_json(team={"id": 456}))

        assert tag.team == Team(
            id=456, club_name=None, team_name=None, nationality=None, club_id=None
        )

    def test_unknown_top_level_fields_are_ignored(self) -> None:
        tag = parse_tag(_tag_json(someFutureField="whatever"))

        assert tag.id == 1

    def test_missing_id_raises(self) -> None:
        raw = _tag_json()
        del raw["id"]

        with pytest.raises(TagParseError, match="'id'"):
            parse_tag(raw)

    def test_non_integer_id_raises(self) -> None:
        with pytest.raises(TagParseError, match="'id'"):
            parse_tag(_tag_json(id="not-an-int"))

    def test_missing_start_time_raises(self) -> None:
        raw = _tag_json()
        del raw["startTime"]

        with pytest.raises(TagParseError, match="startTime"):
            parse_tag(raw)

    def test_malformed_start_time_raises(self) -> None:
        with pytest.raises(TagParseError, match="startTime"):
            parse_tag(_tag_json(startTime="not-a-date"))

    def test_missing_competition_raises(self) -> None:
        raw = _tag_json()
        del raw["competition"]

        with pytest.raises(TagParseError, match="competition"):
            parse_tag(raw)

    def test_competition_missing_id_raises(self) -> None:
        with pytest.raises(TagParseError, match=r"competition\.id"):
            parse_tag(_tag_json(competition={"name": "X", "type": "INTERNATIONAL"}))


def _page_response(
    tags: list[dict[str, Any]], *, page: int, limit: int, total: int, has_next: bool
) -> dict[str, Any]:
    return {
        "data": tags,
        "meta": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": -(-total // limit) if limit else 0,
            "hasNext": has_next,
            "hasPrevious": page > 1,
        },
    }


class TestApiClientGet:
    def test_success_returns_body(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response([_tag_json()], page=1, limit=100, total=1, has_next=False),
        )
        client = ApiClient(API_BASE_URL, "tok")

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [1]

    def test_retries_once_on_timeout_then_succeeds(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        params = {
            "startTimeFrom": "2024-10-05T10:00:00+00:00",
            "endTimeTo": "2024-10-05T11:00:00+00:00",
            "includeOpen": "true",
            "page": "1",
            "limit": "100",
        }
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out"),
            url=f"{API_BASE_URL}/tags",
            match_params=params,
        )
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params=params,
            json=_page_response([_tag_json()], page=1, limit=100, total=1, has_next=False),
        )
        clock = FakeClock()
        client = ApiClient(API_BASE_URL, "tok", sleep=clock.sleep)

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [1]
        assert clock.sleeps == [2.0]

    def test_raises_after_exhausting_retry(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_exception(
            httpx.ConnectTimeout("timed out"),
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            is_reusable=True,
        )
        clock = FakeClock()
        client = ApiClient(API_BASE_URL, "tok", sleep=clock.sleep)

        with pytest.raises(ApiError):
            client.fetch_tags_window(123, start, end)

        assert clock.sleeps == [2.0]

    def test_unauthorized_error_raises_unauthorized(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            status_code=401,
            json={"success": False, "error": "Unauthorized", "message": "no token"},
        )
        client = ApiClient(API_BASE_URL, "tok")

        with pytest.raises(Unauthorized, match="no token"):
            client.fetch_tags_window(123, start, end)

    def test_generic_error_raises_api_error(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            status_code=400,
            json={"success": False, "error": "Validation failed", "message": "bad query"},
        )
        client = ApiClient(API_BASE_URL, "tok")

        with pytest.raises(ApiError, match="bad query"):
            client.fetch_tags_window(123, start, end)

    def test_non_json_body_raises_api_error(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            content=b"not json",
        )
        client = ApiClient(API_BASE_URL, "tok")

        with pytest.raises(ApiError, match="Invalid response"):
            client.fetch_tags_window(123, start, end)

    def test_non_object_body_raises_api_error(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=[1, 2, 3],
        )
        client = ApiClient(API_BASE_URL, "tok")

        with pytest.raises(ApiError, match="expected a JSON object"):
            client.fetch_tags_window(123, start, end)

    def test_context_manager_closes_owned_client(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response([_tag_json()], page=1, limit=100, total=1, has_next=False),
        )

        with ApiClient(API_BASE_URL, "tok") as client:
            tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [1]
        assert client._client.is_closed

    def test_close_leaves_externally_provided_client_open(self) -> None:
        external_client = httpx.Client()
        client = ApiClient(API_BASE_URL, "tok", client=external_client)

        client.close()

        assert not external_client.is_closed
        external_client.close()


class TestFetchTagsWindow:
    def test_single_page_includes_window_params(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response([_tag_json()], page=1, limit=100, total=1, has_next=False),
        )
        client = ApiClient(API_BASE_URL, "tok")

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [1]

    def test_assembles_pages_until_exhausted(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 12, 0, tzinfo=UTC)
        base_params = {
            "startTimeFrom": "2024-10-05T10:00:00+00:00",
            "endTimeTo": "2024-10-05T12:00:00+00:00",
            "includeOpen": "true",
        }
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={**base_params, "page": "1", "limit": "100"},
            json=_page_response(
                [_tag_json(id=1, startTime="2024-10-05T11:00:00.000Z")],
                page=1,
                limit=100,
                total=3,
                has_next=True,
            ),
        )
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={**base_params, "page": "2", "limit": "100"},
            json=_page_response(
                [_tag_json(id=2, startTime="2024-10-05T10:30:00.000Z")],
                page=2,
                limit=100,
                total=3,
                has_next=True,
            ),
        )
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={**base_params, "page": "3", "limit": "100"},
            json=_page_response(
                [_tag_json(id=3, startTime="2024-10-05T10:15:00.000Z")],
                page=3,
                limit=100,
                total=3,
                has_next=False,
            ),
        )
        client = ApiClient(API_BASE_URL, "tok")

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [3, 2, 1]

    def test_filters_out_other_competitions(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response(
                [
                    _tag_json(id=1, competition={"id": 123, "name": "A", "type": "INTERNATIONAL"}),
                    _tag_json(id=2, competition={"id": 999, "name": "B", "type": "INTERNATIONAL"}),
                ],
                page=1,
                limit=100,
                total=2,
                has_next=False,
            ),
        )
        client = ApiClient(API_BASE_URL, "tok")

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [1]

    def test_unparseable_tag_is_skipped_with_warning(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        bad = _tag_json(id=1)
        del bad["startTime"]
        good = _tag_json(id=2)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response([bad, good], page=1, limit=100, total=2, has_next=False),
        )
        client = ApiClient(API_BASE_URL, "tok")

        with caplog.at_level("WARNING"):
            tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [2]
        assert any("Skipping unparseable tag" in record.message for record in caplog.records)

    def test_unauthorized_error_raises_unauthorized(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 11, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T11:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            status_code=401,
            json={"success": False, "error": "Unauthorized", "message": "no token"},
        )
        client = ApiClient(API_BASE_URL, "tok")

        with pytest.raises(Unauthorized, match="no token"):
            client.fetch_tags_window(123, start, end)

    def test_results_are_sorted_by_start_time(self, httpx_mock: HTTPXMock) -> None:
        start = datetime(2024, 10, 5, 10, 0, tzinfo=UTC)
        end = datetime(2024, 10, 5, 12, 0, tzinfo=UTC)
        httpx_mock.add_response(
            url=f"{API_BASE_URL}/tags",
            match_params={
                "startTimeFrom": "2024-10-05T10:00:00+00:00",
                "endTimeTo": "2024-10-05T12:00:00+00:00",
                "includeOpen": "true",
                "page": "1",
                "limit": "100",
            },
            json=_page_response(
                [
                    _tag_json(id=1, startTime="2024-10-05T11:30:00.000Z"),
                    _tag_json(id=2, startTime="2024-10-05T10:15:00.000Z"),
                    _tag_json(id=3, startTime="2024-10-05T10:45:00.000Z"),
                ],
                page=1,
                limit=100,
                total=3,
                has_next=False,
            ),
        )
        client = ApiClient(API_BASE_URL, "tok")

        tags = client.fetch_tags_window(123, start, end)

        assert [tag.id for tag in tags] == [2, 3, 1]
