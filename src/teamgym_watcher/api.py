"""httpx client for the teamgym-sorter Tags API: paginated fetch with retry-on-cold-start.

Ports the retry/timeout behaviour of ``TagsAPI.lua``/``ApiConfig.lua``: a single retry after
a fixed delay on a network-level failure (timeout or connection error), modelling Lambda cold
starts. Response bodies are parsed regardless of HTTP status code, matching the sorter API's
``{success, error, message}`` envelope on error responses at any status (see ``main-api.yaml``
and the parity note in ``auth.py``'s ``_parse_response_body``) — a successful ``GET /tags``
response has no ``success`` key at all (just ``data``/``meta``), only error responses set
``success: false``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS: Final = 30.0
# ApiConfig.HTTP.RETRY_COUNT / RETRY_DELAY_SECONDS
RETRY_COUNT: Final = 1
RETRY_DELAY_SECONDS: Final = 2.0
DEFAULT_PAGE_LIMIT: Final = 100


class ApiError(Exception):
    """Base class for Tags API failures."""


class Unauthorized(ApiError):
    """The API rejected the request as unauthenticated (``error == "Unauthorized"``).

    Callers map this to a re-authentication prompt, same as the plugin's
    ``authentication_expired`` handling in ``TagsAPI.lua``.
    """


class TagParseError(ApiError):
    """A tag JSON object could not be parsed into a :class:`Tag`."""


@dataclass(frozen=True, slots=True)
class Team:
    """``tag.team`` — absent for tags that aren't team-specific (e.g. AWARDS)."""

    id: int | None
    club_name: str | None
    team_name: str | None
    nationality: str | None
    club_id: int | None


@dataclass(frozen=True, slots=True)
class Group:
    """``tag.group`` — populated for section groups and (always) Club-competition tags."""

    name: str | None
    round_type: str | None


@dataclass(frozen=True, slots=True)
class Award:
    """``tag.award`` — populated only for tags associated with an award."""

    name: str | None


@dataclass(frozen=True, slots=True)
class Competition:
    """``tag.competition`` display fields; ``id`` is surfaced on :class:`Tag` instead."""

    name: str | None
    type: str | None


@dataclass(frozen=True, slots=True)
class Tag:
    """A competition tag, as read by ``MetadataMapper.lua``/``BulkPhotoTagger.lua``."""

    id: int
    competition_id: int
    start_time: datetime
    end_time: datetime | None
    section: str | None
    age_group: str | None
    phase: str | None
    apparatus: str | None
    team: Team | None
    group: Group | None
    award: Award | None
    competition: Competition


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TagParseError(f"'{field}' must be a string, got {value!r}.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TagParseError(f"'{field}' is not a valid ISO 8601 timestamp: {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_team(raw: object) -> Team | None:
    if not isinstance(raw, dict):
        return None
    return Team(
        id=_optional_int(raw.get("id")),
        club_name=_optional_str(raw.get("clubName")),
        team_name=_optional_str(raw.get("teamName")),
        nationality=_optional_str(raw.get("nationality")),
        club_id=_optional_int(raw.get("clubId")),
    )


def _parse_group(raw: object) -> Group | None:
    if not isinstance(raw, dict):
        return None
    return Group(
        name=_optional_str(raw.get("name")), round_type=_optional_str(raw.get("roundType"))
    )


def _parse_award(raw: object) -> Award | None:
    if not isinstance(raw, dict):
        return None
    return Award(name=_optional_str(raw.get("name")))


def _parse_competition(raw: object) -> tuple[Competition, int]:
    if not isinstance(raw, dict):
        raise TagParseError(f"'competition' must be an object, got {raw!r}.")
    competition_id = _optional_int(raw.get("id"))
    if competition_id is None:
        raise TagParseError(f"'competition.id' must be an integer, got {raw.get('id')!r}.")
    return (
        Competition(name=_optional_str(raw.get("name")), type=_optional_str(raw.get("type"))),
        competition_id,
    )


def parse_tag(raw: dict[str, Any]) -> Tag:
    """Parse a single tag JSON object into a :class:`Tag`.

    Raises :class:`TagParseError` if a required field (``id``, ``startTime``,
    ``competition.id``) is missing or malformed. Optional sub-objects (``team``/``group``/
    ``award``) that are absent, null, or missing their own fields degrade to ``None`` rather
    than raising, matching the plugin's field-by-field defensive reads in
    ``MetadataMapper.lua``.
    """
    tag_id = _optional_int(raw.get("id"))
    if tag_id is None:
        raise TagParseError(f"'id' must be an integer, got {raw.get('id')!r}.")

    start_time = _parse_datetime(raw.get("startTime"), "startTime")
    end_time_raw = raw.get("endTime")
    end_time = _parse_datetime(end_time_raw, "endTime") if end_time_raw is not None else None

    competition, competition_id = _parse_competition(raw.get("competition"))

    return Tag(
        id=tag_id,
        competition_id=competition_id,
        start_time=start_time,
        end_time=end_time,
        section=_optional_str(raw.get("section")),
        age_group=_optional_str(raw.get("ageGroup")),
        phase=_optional_str(raw.get("phase")),
        apparatus=_optional_str(raw.get("apparatus")),
        team=_parse_team(raw.get("team")),
        group=_parse_group(raw.get("group")),
        award=_parse_award(raw.get("award")),
        competition=competition,
    )


def _parse_response_body(response: httpx.Response, url: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(f"Invalid response from {url}: {exc}") from exc
    if not isinstance(body, dict):
        raise ApiError(f"Invalid response from {url}: expected a JSON object.")

    if body.get("success") is False:
        error = body.get("error")
        message = str(body.get("message", "Tags API request failed."))
        if error == "Unauthorized":
            raise Unauthorized(message)
        raise ApiError(message)

    return body


class ApiClient:
    """httpx-based client for the teamgym-sorter Tags API.

    ``sleep`` is injectable so tests never perform a real sleep during the cold-start retry.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, path: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

        attempt = 1
        while True:
            try:
                response = self._client.get(url, headers=headers, params=params)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt > RETRY_COUNT:
                    raise ApiError(
                        f"Request to {url} failed after {attempt} attempt(s): {exc}"
                    ) from exc
                logger.warning(
                    "Request to %s failed (attempt %d); retrying in %.0fs: %s",
                    url,
                    attempt,
                    RETRY_DELAY_SECONDS,
                    exc,
                )
                self._sleep(RETRY_DELAY_SECONDS)
                attempt += 1
                continue
            return _parse_response_body(response, url)

    def fetch_all_tags(self, competition_id: int, *, limit: int = DEFAULT_PAGE_LIMIT) -> list[Tag]:
        """Fetch every tag for ``competition_id``, following pagination until exhausted.

        The ``/tags`` endpoint has no ``competitionId`` filter, so results are filtered
        client-side, and sorted by ``startTime`` to feed the sorted-merge matcher (step 07).
        A tag that fails to parse is logged and skipped rather than aborting the whole fetch,
        so one malformed record never blocks all photo matching.
        """
        tags: list[Tag] = []
        page = 1
        while True:
            body = self._get("/tags", {"page": page, "limit": limit})

            raw_tags = body.get("data")
            if not isinstance(raw_tags, list):
                raw_tags = []
            for raw_tag in raw_tags:
                if not isinstance(raw_tag, dict):
                    logger.warning("Skipping non-object tag entry: %r", raw_tag)
                    continue
                try:
                    tag = parse_tag(raw_tag)
                except TagParseError as exc:
                    logger.warning("Skipping unparseable tag: %s", exc)
                    continue
                if tag.competition_id == competition_id:
                    tags.append(tag)

            meta = body.get("meta")
            has_next = isinstance(meta, dict) and bool(meta.get("hasNext"))
            if not has_next:
                break
            page += 1

        tags.sort(key=lambda tag: tag.start_time)
        return tags
