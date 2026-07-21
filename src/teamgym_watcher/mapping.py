"""Port of ``MetadataMapper.lua``: translates a :class:`~teamgym_watcher.api.Tag` into the
plugin's metadata fields with identical values.

Parity with the Lua is the entire point of this module (see CLAUDE.md) — every mapping table,
warning, and edge case below mirrors ``MetadataMapper.lua`` exactly, including its asymmetries
(see the ``ClubName``/``TeamName`` note on :func:`map_tag_to_metadata`). Lua truthiness treats
``""`` as truthy (only ``nil``/``false`` are falsy), so checks below use ``is not None`` rather
than Python truthiness wherever the Lua used a plain ``if value then`` guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from teamgym_watcher.api import Tag

logger = logging.getLogger(__name__)

SECTION_MAPPING: Final[dict[str, str]] = {
    "MIXED": "Mixed",
    "MEN": "Men",
    "WOMEN": "Women",
}

AGE_GROUP_MAPPING: Final[dict[str, str]] = {
    "JUNIOR": "Junior",
    "SENIOR": "Senior",
}

APPARATUS_MAPPING: Final[dict[str, str]] = {
    "FLOOR": "Floor",
    "TUMBLE": "Tumble",  # API sometimes uses TUMBLE instead of TUMBLING
    "TRAMPET": "Trampet",
}

PHASE_MAPPING: Final[dict[str, str]] = {
    "QUALIFICATION_WARMUP": "QualificationWarmup",  # INTERNATIONAL-only warmup phase
    "QUALIFICATION": "Qualification",
    "FINALS": "Competition",  # API uses FINALS for competition finals
    "FINALS_WARMUP": "CompetitionWarmup",  # API warmup phase for finals
    "PODIUM_TRAINING": "PodiumTraining",
    "PODIUM_TRAINING_WARMUP": "PodiumTrainingWarmup",  # API warmup phase for podium training
    "WARM_UP": "CompetitionWarmup",  # Legacy mapping
    "TRAVELLING": "Travelling",
    "BANQUET": "Banquet",
    "OPENING_CEREMONY": "OpeningCeremony",
    "AWARDS": "Awards",
}

COMPETITION_TYPE_MAPPING: Final[dict[str, str]] = {
    "INTERNATIONAL": "International",
    "CLUB": "Club",
    "INTERNATIONAL_CLUB": "InternationalClub",
}

# Nationality mapping from IOC/ISO codes to full names as defined in
# MyMetadataDefinitionFile.lua. Canonical (IOC) codes and names are kept in sync with the
# server's COUNTRIES_MAP (server/src/lib/countries.ts); non-IOC alternative codes are kept as
# aliases for legacy tagged data.
NATIONALITY_MAPPING: Final[dict[str, str]] = {
    "AUT": "Austria",
    "AZE": "Azerbaijan",
    "BEL": "Belgium",
    "BUL": "Bulgaria",  # IOC code (canonical, matches server)
    "BGR": "Bulgaria",  # Alternative ISO code for Bulgaria
    "CRO": "Croatia",  # IOC code (canonical, matches server)
    "CZE": "Czech Republic",
    "DNK": "Denmark",
    "DEN": "Denmark",  # Alternative code sometimes used
    "EST": "Estonia",
    "FIN": "Finland",
    "FRA": "France",
    "GBR": "Great Britain",
    "GER": "Germany",
    "DEU": "Germany",  # Alternative ISO code for Germany
    "HUN": "Hungary",
    "ISL": "Iceland",
    "ITA": "Italy",
    "LAT": "Latvia",
    "LVA": "Latvia",  # Alternative ISO code for Latvia
    "LTU": "Lithuania",
    "LUX": "Luxembourg",
    "NLD": "Netherlands",
    "NED": "Netherlands",  # Alternative code sometimes used
    "NOR": "Norway",
    "POL": "Poland",
    "PRT": "Portugal",
    "POR": "Portugal",  # Alternative code sometimes used
    "ROU": "Romania",
    "ROM": "Romania",  # Alternative code sometimes used
    "SVK": "Slovakia",
    "SLO": "Slovenia",
    "SVN": "Slovenia",  # Alternative ISO code for Slovenia
    "ESP": "Spain",
    "SWE": "Sweden",
    "SUI": "Switzerland",
    "CHE": "Switzerland",  # Alternative ISO code for Switzerland
    "USA": "United States",
}

# Preferred code when nationality_to_olympic_code() reverses a name shared by two aliases
# (e.g. "Germany" is reachable via both GER and DEU). Ported verbatim from the explicit
# overrides applied after nationalityToOlympicCode()'s reverse-mapping build in the Lua.
_OLYMPIC_CODE_OVERRIDES: Final[dict[str, str]] = {
    "Bulgaria": "BUL",
    "Denmark": "DEN",
    "Germany": "GER",
    "Latvia": "LAT",
    "Netherlands": "NED",
    "Portugal": "POR",
    "Romania": "ROU",
    "Slovenia": "SVN",
    "Switzerland": "SUI",
}


def _build_reverse_nationality_mapping() -> dict[str, str]:
    reverse: dict[str, str] = {}
    for code, name in NATIONALITY_MAPPING.items():
        reverse.setdefault(name, code)
    reverse.update(_OLYMPIC_CODE_OVERRIDES)
    return reverse


_REVERSE_NATIONALITY_MAPPING: Final[dict[str, str]] = _build_reverse_nationality_mapping()


@dataclass(frozen=True, slots=True)
class MapResult:
    """Result of mapping one or more tags to plugin metadata field values.

    ``metadata`` keys are the plugin field ids from ``MyMetadataDefinitionFile.lua`` (Section,
    Age, Competition, Apparatus, Phase, ClubName, TeamName, Nationality, CompetitionType,
    AwardsSection, Group, RoundType); a field is simply absent when its source value was
    unmapped or missing, mirroring the Lua's plain Lua table (no key = unset).
    """

    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def map_tag_to_metadata(tag: Tag) -> MapResult:
    """Translate a single tag into plugin metadata fields, exactly as ``mapTagToMetadata`` does.

    Two asymmetries are intentional ports of the Lua, not bugs to "fix":

    - ``ClubName`` is only set when ``team.club_name`` is present *and* non-empty; when a team
      exists but has no club name, the Lua only logs (``logger:warn``) and does **not** append
      to the returned warnings list, so no warning appears here either. See
      ``docs/deviations.md`` for why this is kept despite looking inconsistent.
    - ``TeamName`` has no such non-empty check — it is set whenever ``team.team_name`` is not
      ``None``, even if it is an empty string.
    - An unmapped nationality code is used as-is (unlike section/age/apparatus/phase/
      competition-type, which are omitted) plus a warning.
    """
    metadata: dict[str, str] = {}
    warnings: list[str] = []

    if tag.section is not None:
        mapped_section = SECTION_MAPPING.get(tag.section)
        if mapped_section is not None:
            metadata["Section"] = mapped_section
        else:
            warnings.append(f"Unknown section value: {tag.section}")

    if tag.age_group is not None:
        mapped_age = AGE_GROUP_MAPPING.get(tag.age_group)
        if mapped_age is not None:
            metadata["Age"] = mapped_age
        else:
            warnings.append(f"Unknown age group value: {tag.age_group}")

    if tag.competition.name is not None:
        metadata["Competition"] = tag.competition.name

    if tag.apparatus is not None:
        mapped_apparatus = APPARATUS_MAPPING.get(tag.apparatus)
        if mapped_apparatus is not None:
            metadata["Apparatus"] = mapped_apparatus
        else:
            warnings.append(f"Unknown apparatus value: {tag.apparatus}")

    if tag.phase is not None:
        mapped_phase = PHASE_MAPPING.get(tag.phase)
        if mapped_phase is not None:
            metadata["Phase"] = mapped_phase
        else:
            warnings.append(f"Unknown phase value: {tag.phase}")

    if tag.team is not None:
        if tag.team.club_name is not None and tag.team.club_name != "":
            metadata["ClubName"] = tag.team.club_name
        else:
            logger.debug("Team object exists but clubName is missing or empty")

        if tag.team.team_name is not None:
            metadata["TeamName"] = tag.team.team_name

        if tag.team.nationality is not None:
            mapped_nationality = NATIONALITY_MAPPING.get(tag.team.nationality)
            if mapped_nationality is not None:
                metadata["Nationality"] = mapped_nationality
            else:
                metadata["Nationality"] = tag.team.nationality
                warnings.append(f"Unknown nationality code: {tag.team.nationality}")

    if tag.competition.type is not None:
        mapped_competition_type = COMPETITION_TYPE_MAPPING.get(tag.competition.type)
        if mapped_competition_type is not None:
            metadata["CompetitionType"] = mapped_competition_type
        else:
            warnings.append(f"Unknown competition type: {tag.competition.type}")

    if tag.award is not None and tag.award.name is not None:
        metadata["AwardsSection"] = tag.award.name

    if tag.group is not None and tag.group.name is not None:
        metadata["Group"] = tag.group.name

    if tag.group is not None and tag.group.round_type is not None:
        metadata["RoundType"] = tag.group.round_type

    if warnings:
        logger.warning("Mapped tag %s with warnings: %s", tag.id, warnings)

    return MapResult(metadata=metadata, warnings=warnings)


def map_tags_to_metadata(tags: Sequence[Tag]) -> MapResult:
    """First-match-wins mapping, mirroring ``mapTagsToMetadata``.

    The API guarantees tags never overlap in time, so whichever tag matched a photo first is
    the only one that matters; an empty sequence maps to an empty, warning-free result.
    """
    if not tags:
        return MapResult(metadata={}, warnings=[])
    return map_tag_to_metadata(tags[0])


def nationality_to_olympic_code(country_name: str | None) -> str | None:
    """Reverse of :data:`NATIONALITY_MAPPING`: full country name -> Olympic 3-letter code.

    Ports ``nationalityToOlympicCode`` including its explicit preferred-code overrides for
    names reachable via two aliases (:data:`_OLYMPIC_CODE_OVERRIDES`). An unrecognised name is
    returned unchanged (logged, not raised) — the Lua does the same rather than failing.
    """
    if country_name is None:
        return None

    olympic_code = _REVERSE_NATIONALITY_MAPPING.get(country_name)
    if olympic_code is not None:
        return olympic_code

    logger.warning("No Olympic code found for nationality: %s", country_name)
    return country_name
