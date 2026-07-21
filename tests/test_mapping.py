"""Tests for mapping.py: the port of MetadataMapper.lua.

Ported cases cite the Lua test name from ``MetadataMapperTests.lua`` in a comment above the
Python test. Additional tests cover every enum value in ``MyMetadataDefinitionFile.lua``, every
nationality alias code, and both directions of the Olympic-code map, per prompts/05.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from teamgym_watcher.api import Award, Competition, Group, Tag, Team
from teamgym_watcher.mapping import (
    AGE_GROUP_MAPPING,
    APPARATUS_MAPPING,
    COMPETITION_TYPE_MAPPING,
    NATIONALITY_MAPPING,
    PHASE_MAPPING,
    SECTION_MAPPING,
    map_tag_to_metadata,
    map_tags_to_metadata,
    nationality_to_olympic_code,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)

# MyMetadataDefinitionFile.lua enum `values` (excluding the Unset/Nil entry) for each field.
_SECTION_ENUM_VALUES = {"Men", "Women", "Mixed"}
_AGE_ENUM_VALUES = {"Junior", "Senior"}
_APPARATUS_ENUM_VALUES = {"Tumble", "Trampet", "Floor"}
_PHASE_ENUM_VALUES = {
    "Travelling",
    "PodiumTraining",
    "PodiumTrainingWarmup",
    "QualificationWarmup",
    "Qualification",
    "Competition",
    "CompetitionWarmup",
    "Awards",
    "Banquet",
    "OpeningCeremony",
}
_COMPETITION_TYPE_ENUM_VALUES = {"Club", "International", "InternationalClub"}
_NATIONALITY_ENUM_VALUES = {
    "Austria",
    "Azerbaijan",
    "Belgium",
    "Bulgaria",
    "Croatia",
    "Czech Republic",
    "Denmark",
    "Estonia",
    "Finland",
    "France",
    "Germany",
    "Great Britain",
    "Hungary",
    "Iceland",
    "Italy",
    "Latvia",
    "Lithuania",
    "Luxembourg",
    "Netherlands",
    "Norway",
    "Poland",
    "Portugal",
    "Romania",
    "Slovakia",
    "Slovenia",
    "Spain",
    "Sweden",
    "Switzerland",
    "United States",
}
_ROUND_TYPE_ENUM_VALUES = {"FULL", "MICRO"}

# server/src/lib/countries.ts COUNTRIES_MAP, mirrored from MetadataMapperTests.lua's
# SERVER_COUNTRIES table.
_SERVER_COUNTRIES = {
    "AUT": "Austria",
    "BEL": "Belgium",
    "BUL": "Bulgaria",
    "CRO": "Croatia",
    "CZE": "Czech Republic",
    "DEN": "Denmark",
    "EST": "Estonia",
    "FIN": "Finland",
    "FRA": "France",
    "GER": "Germany",
    "GBR": "Great Britain",
    "HUN": "Hungary",
    "ITA": "Italy",
    "LAT": "Latvia",
    "LTU": "Lithuania",
    "LUX": "Luxembourg",
    "NED": "Netherlands",
    "NOR": "Norway",
    "POL": "Poland",
    "POR": "Portugal",
    "ROU": "Romania",
    "SVK": "Slovakia",
    "SLO": "Slovenia",
    "ESP": "Spain",
    "SWE": "Sweden",
    "SUI": "Switzerland",
    "USA": "United States",
}


def _international_tag(
    *,
    section: str | None = "WOMEN",
    age_group: str | None = "SENIOR",
    phase: str | None = "QUALIFICATION",
    apparatus: str | None = "TRAMPET",
    competition: Competition | None = None,
    team: Team | None = None,
) -> Tag:
    """Mirrors ``buildInternationalTag`` from MetadataMapperTests.lua."""
    if competition is None:
        competition = Competition(name="World Championships 2026", type="INTERNATIONAL")
    if team is None:
        team = Team(id=42, club_name="", nationality="GBR", team_name=None, club_id=None)
    return Tag(
        id=123,
        competition_id=7,
        start_time=_START,
        end_time=None,
        section=section,
        age_group=age_group,
        phase=phase,
        apparatus=apparatus,
        team=team,
        group=None,
        award=None,
        competition=competition,
    )


def _minimal_tag(
    *,
    section: str | None = None,
    age_group: str | None = None,
    apparatus: str | None = None,
    phase: str | None = None,
    competition: Competition | None = None,
    team: Team | None = None,
    group: Group | None = None,
    award: Award | None = None,
) -> Tag:
    return Tag(
        id=1,
        competition_id=1,
        start_time=_START,
        end_time=None,
        section=section,
        age_group=age_group,
        phase=phase,
        apparatus=apparatus,
        team=team,
        group=group,
        award=award,
        competition=competition if competition is not None else Competition(name=None, type=None),
    )


class TestPortedFromLua:
    """One test per function in MetadataMapperTests.lua; names transliterated to snake_case."""

    def test_qualification_warmup_phase_mapping(self) -> None:
        # Lua: testQualificationWarmupPhaseMapping
        tag = _international_tag(phase="QUALIFICATION_WARMUP")

        result = map_tag_to_metadata(tag)

        assert result.metadata["Phase"] == "QualificationWarmup"

    def test_qualification_phase_still_maps_unchanged(self) -> None:
        # Lua: testQualificationPhaseStillMapsUnchanged
        tag = _international_tag(phase="QUALIFICATION")

        result = map_tag_to_metadata(tag)

        assert result.metadata["Phase"] == "Qualification"

    def test_international_team_empty_club_name_produces_nationality_only(self) -> None:
        # Lua: testInternationalTeamEmptyClubNameProducesNationalityOnly
        tag = _international_tag(
            team=Team(id=42, club_name="", nationality="GBR", team_name=None, club_id=None)
        )

        result = map_tag_to_metadata(tag)

        assert result.metadata["Nationality"] == "Great Britain"
        assert "ClubName" not in result.metadata
        assert "TeamName" not in result.metadata

    def test_club_team_still_gets_club_name_and_team_name(self) -> None:
        # Lua: testClubTeamStillGetsClubNameAndTeamName — regression: CLUB competitions must
        # remain unaffected by the INTERNATIONAL guard.
        tag = _international_tag(
            competition=Competition(name="National Club Championships", type="CLUB"),
            team=Team(
                id=20,
                club_name="Stockholm Gymnastics",
                nationality=None,
                team_name="Stockholm Stars A",
                club_id=5,
            ),
        )

        result = map_tag_to_metadata(tag)

        assert result.metadata["ClubName"] == "Stockholm Gymnastics"
        assert result.metadata["TeamName"] == "Stockholm Stars A"
        assert "Nationality" not in result.metadata

    @pytest.mark.parametrize(
        ("code", "expected_name"), sorted(_SERVER_COUNTRIES.items()), ids=lambda v: v
    )
    def test_all_server_country_codes_resolve_to_server_names(
        self, code: str, expected_name: str
    ) -> None:
        # Lua: testAllServerCountryCodesResolveToServerNames
        tag = _international_tag(
            team=Team(id=1, club_name="", nationality=code, team_name=None, club_id=None)
        )

        result = map_tag_to_metadata(tag)

        assert result.metadata["Nationality"] == expected_name


class TestEnumRoundTrips:
    """Every mapping-table output must be a real MyMetadataDefinitionFile.lua enum value."""

    @pytest.mark.parametrize(("api_value", "expected"), sorted(SECTION_MAPPING.items()))
    def test_section(self, api_value: str, expected: str) -> None:
        assert expected in _SECTION_ENUM_VALUES
        result = map_tag_to_metadata(_minimal_tag(section=api_value))
        assert result.metadata["Section"] == expected
        assert result.warnings == []

    @pytest.mark.parametrize(("api_value", "expected"), sorted(AGE_GROUP_MAPPING.items()))
    def test_age_group(self, api_value: str, expected: str) -> None:
        assert expected in _AGE_ENUM_VALUES
        result = map_tag_to_metadata(_minimal_tag(age_group=api_value))
        assert result.metadata["Age"] == expected
        assert result.warnings == []

    @pytest.mark.parametrize(("api_value", "expected"), sorted(APPARATUS_MAPPING.items()))
    def test_apparatus(self, api_value: str, expected: str) -> None:
        assert expected in _APPARATUS_ENUM_VALUES
        result = map_tag_to_metadata(_minimal_tag(apparatus=api_value))
        assert result.metadata["Apparatus"] == expected
        assert result.warnings == []

    @pytest.mark.parametrize(("api_value", "expected"), sorted(PHASE_MAPPING.items()))
    def test_phase(self, api_value: str, expected: str) -> None:
        assert expected in _PHASE_ENUM_VALUES
        result = map_tag_to_metadata(_minimal_tag(phase=api_value))
        assert result.metadata["Phase"] == expected
        assert result.warnings == []

    @pytest.mark.parametrize(("api_value", "expected"), sorted(COMPETITION_TYPE_MAPPING.items()))
    def test_competition_type(self, api_value: str, expected: str) -> None:
        assert expected in _COMPETITION_TYPE_ENUM_VALUES
        tag = _minimal_tag(competition=Competition(name="X", type=api_value))
        result = map_tag_to_metadata(tag)
        assert result.metadata["CompetitionType"] == expected
        assert result.warnings == []

    @pytest.mark.parametrize(
        ("code", "expected_name"), sorted(NATIONALITY_MAPPING.items()), ids=lambda v: v
    )
    def test_nationality_every_alias_code(self, code: str, expected_name: str) -> None:
        assert expected_name in _NATIONALITY_ENUM_VALUES
        tag = _minimal_tag(
            team=Team(id=1, club_name="", nationality=code, team_name=None, club_id=None)
        )
        result = map_tag_to_metadata(tag)
        assert result.metadata["Nationality"] == expected_name
        assert result.warnings == []

    @pytest.mark.parametrize("round_type", sorted(_ROUND_TYPE_ENUM_VALUES))
    def test_round_type_passes_through_unchanged(self, round_type: str) -> None:
        tag = _minimal_tag(group=Group(name="Group A", round_type=round_type))
        result = map_tag_to_metadata(tag)
        assert result.metadata["RoundType"] == round_type
        assert result.metadata["Group"] == "Group A"


class TestMapTagToMetadata:
    def test_competition_name_is_direct_string_mapping(self) -> None:
        tag = _minimal_tag(competition=Competition(name="Worlds 2026", type=None))
        result = map_tag_to_metadata(tag)
        assert result.metadata["Competition"] == "Worlds 2026"

    def test_award_name_maps_to_awards_section(self) -> None:
        tag = _minimal_tag(award=Award(name="Gold"))
        result = map_tag_to_metadata(tag)
        assert result.metadata["AwardsSection"] == "Gold"

    def test_no_team_produces_no_team_fields(self) -> None:
        result = map_tag_to_metadata(_minimal_tag(team=None))
        assert "ClubName" not in result.metadata
        assert "TeamName" not in result.metadata
        assert "Nationality" not in result.metadata

    def test_team_without_club_name_produces_no_warning_entry(self) -> None:
        tag = _minimal_tag(
            team=Team(id=1, club_name=None, nationality=None, team_name=None, club_id=None)
        )
        result = map_tag_to_metadata(tag)
        assert "ClubName" not in result.metadata
        assert result.warnings == []

    def test_team_with_empty_club_name_produces_no_warning_entry(self) -> None:
        tag = _minimal_tag(
            team=Team(id=1, club_name="", nationality=None, team_name=None, club_id=None)
        )
        result = map_tag_to_metadata(tag)
        assert "ClubName" not in result.metadata
        assert result.warnings == []

    def test_team_name_set_even_when_empty_string(self) -> None:
        tag = _minimal_tag(
            team=Team(id=1, club_name=None, nationality=None, team_name="", club_id=None)
        )
        result = map_tag_to_metadata(tag)
        assert result.metadata["TeamName"] == ""

    def test_unknown_section_value_warns_and_omits_field(self) -> None:
        result = map_tag_to_metadata(_minimal_tag(section="ALIEN"))
        assert "Section" not in result.metadata
        assert "Unknown section value: ALIEN" in result.warnings

    def test_unknown_age_group_value_warns_and_omits_field(self) -> None:
        result = map_tag_to_metadata(_minimal_tag(age_group="ANCIENT"))
        assert "Age" not in result.metadata
        assert "Unknown age group value: ANCIENT" in result.warnings

    def test_unknown_apparatus_value_warns_and_omits_field(self) -> None:
        result = map_tag_to_metadata(_minimal_tag(apparatus="BEAM"))
        assert "Apparatus" not in result.metadata
        assert "Unknown apparatus value: BEAM" in result.warnings

    def test_unknown_phase_value_warns_and_omits_field(self) -> None:
        result = map_tag_to_metadata(_minimal_tag(phase="LUNCH"))
        assert "Phase" not in result.metadata
        assert "Unknown phase value: LUNCH" in result.warnings

    def test_unknown_competition_type_warns_and_omits_field(self) -> None:
        tag = _minimal_tag(competition=Competition(name="X", type="INTERGALACTIC"))
        result = map_tag_to_metadata(tag)
        assert "CompetitionType" not in result.metadata
        assert "Unknown competition type: INTERGALACTIC" in result.warnings

    def test_unknown_nationality_code_used_as_is_with_warning(self) -> None:
        tag = _minimal_tag(
            team=Team(id=1, club_name="", nationality="XYZ", team_name=None, club_id=None)
        )
        result = map_tag_to_metadata(tag)
        assert result.metadata["Nationality"] == "XYZ"
        assert "Unknown nationality code: XYZ" in result.warnings


class TestMapTagsToMetadata:
    def test_empty_sequence_returns_empty_result(self) -> None:
        result = map_tags_to_metadata([])
        assert result.metadata == {}
        assert result.warnings == []

    def test_uses_first_tag_only(self) -> None:
        first = _minimal_tag(section="MEN")
        second = _minimal_tag(section="WOMEN")

        result = map_tags_to_metadata([first, second])

        assert result.metadata["Section"] == "Men"


class TestNationalityToOlympicCode:
    def test_none_returns_none(self) -> None:
        assert nationality_to_olympic_code(None) is None

    def test_unknown_country_name_passes_through(self) -> None:
        assert nationality_to_olympic_code("Atlantis") == "Atlantis"

    @pytest.mark.parametrize(
        ("name", "expected_code"),
        [
            ("Bulgaria", "BUL"),
            ("Denmark", "DEN"),
            ("Germany", "GER"),
            ("Latvia", "LAT"),
            ("Netherlands", "NED"),
            ("Portugal", "POR"),
            ("Romania", "ROU"),
            ("Slovenia", "SVN"),
            ("Switzerland", "SUI"),
        ],
    )
    def test_preferred_code_overrides_for_aliased_names(
        self, name: str, expected_code: str
    ) -> None:
        assert nationality_to_olympic_code(name) == expected_code

    @pytest.mark.parametrize(("code", "name"), sorted(NATIONALITY_MAPPING.items()), ids=lambda v: v)
    def test_round_trips_back_to_a_code_for_the_same_name(self, code: str, name: str) -> None:
        reverse_code = nationality_to_olympic_code(name)
        assert reverse_code is not None
        assert NATIONALITY_MAPPING[reverse_code] == name
