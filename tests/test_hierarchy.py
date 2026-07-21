"""Tests for hierarchy.py: the port of PhotoCollectionHierarchy.lua's phase-based path
derivation.

Ported cases cite the Lua test name from ``PhotoCollectionHierarchyTests.lua`` in a comment
above the Python test. The Lua's "By Team" tests (``testInternationalByTeam*``) and the
catalog-level mixed-selection validation tests are out of scope: only the phase-based on-disk
hierarchy is ported, per prompts/06.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from teamgym_watcher.hierarchy import (
    PhaseType,
    classify_phase_type,
    derive_club_path,
    derive_destination_dir,
    derive_international_club_path,
    derive_international_path,
    derive_path,
    sanitize_collection_name,
)

COMPETITION_NAME = "World Championships 2026"


class TestPortedFromLua:
    """One test per phase-based case in PhotoCollectionHierarchyTests.lua."""

    def test_international_nested_qualification_full_metadata(self) -> None:
        # Lua: testInternationalNestedQualificationFullMetadata
        metadata = {
            "CompetitionType": "International",
            "Phase": "Qualification",
            "Age": "Senior",
            "Section": "Women",
            "Nationality": "Great Britain",
            "Apparatus": "Trampet",
        }

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == [
            "Competitions",
            COMPETITION_NAME,
            "Qualification",
            "Senior",
            "Women",
            "Great Britain",
            "Trampet",
        ]

    def test_international_nested_qualification_warmup_full_metadata(self) -> None:
        # Lua: testInternationalNestedQualificationWarmupFullMetadata
        metadata = {
            "CompetitionType": "International",
            "Phase": "QualificationWarmup",
            "Age": "Senior",
            "Section": "Women",
            "Nationality": "Great Britain",
            "Apparatus": "Trampet",
        }

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == [
            "Competitions",
            COMPETITION_NAME,
            "Qualification Warmup",
            "Senior",
            "Women",
            "Great Britain",
            "Trampet",
        ]

    def test_international_nested_missing_apparatus_stops_at_random(self) -> None:
        # Lua: testInternationalNestedMissingApparatusStopsAtRandom
        metadata = {
            "CompetitionType": "International",
            "Phase": "Qualification",
            "Age": "Senior",
            "Section": "Women",
            "Nationality": "Great Britain",
        }

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == [
            "Competitions",
            COMPETITION_NAME,
            "Qualification",
            "Senior",
            "Women",
            "Great Britain",
            "Random",
        ]

    def test_international_nested_missing_section_stops_at_random(self) -> None:
        # Lua: testInternationalNestedMissingSectionStopsAtRandom
        metadata = {
            "CompetitionType": "International",
            "Phase": "Qualification",
            "Age": "Senior",
        }

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Qualification", "Senior", "Random"]

    def test_international_flat_phase_travelling(self) -> None:
        # Lua: testInternationalFlatPhaseTravelling
        metadata = {"CompetitionType": "International", "Phase": "Travelling"}

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Travelling"]

    def test_international_flat_phase_opening_ceremony(self) -> None:
        # Lua: testInternationalFlatPhaseOpeningCeremony
        metadata = {"CompetitionType": "International", "Phase": "OpeningCeremony"}

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Opening Ceremony"]

    def test_international_awards_phase(self) -> None:
        # Lua: testInternationalAwardsPhase
        metadata = {
            "CompetitionType": "International",
            "Phase": "Awards",
            "AwardsSection": "Junior Women",
        }

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Awards", "Junior Women"]

    def test_club_nested_hierarchy_unchanged(self) -> None:
        # Lua: testClubNestedHierarchyUnchanged
        metadata = {
            "CompetitionType": "Club",
            "Phase": "Qualification",
            "Group": "Group A",
            "ClubName": "Stockholm Gymnastics",
            "TeamName": "Stockholm Stars A",
            "Apparatus": "Floor",
        }

        path = derive_path(metadata, "National Championships")

        assert path == [
            "Competitions",
            "National Championships",
            "Qualification",
            "Group A",
            "Stockholm Gymnastics - Stockholm Stars A",
            "Floor",
        ]

    def test_international_club_nested_hierarchy_unchanged(self) -> None:
        # Lua: testInternationalClubNestedHierarchyUnchanged
        metadata = {
            "CompetitionType": "InternationalClub",
            "Phase": "Competition",
            "Age": "Junior",
            "Section": "Women",
            "Nationality": "Great Britain",
            "ClubName": "Bracknell",
            "Apparatus": "Tumble",
        }

        path = derive_path(metadata, "European Championships 2026")

        assert path == [
            "Competitions",
            "European Championships 2026",
            "Competition",
            "Junior",
            "Women",
            "GBR - Bracknell",
            "Tumble",
        ]


class TestClassifyPhaseType:
    @pytest.mark.parametrize(
        "phase",
        [
            "PodiumTraining",
            "PodiumTrainingWarmup",
            "QualificationWarmup",
            "Qualification",
            "Competition",
            "CompetitionWarmup",
        ],
    )
    def test_nested_phases(self, phase: str) -> None:
        assert classify_phase_type(phase) is PhaseType.NESTED

    @pytest.mark.parametrize("phase", ["Travelling", "Banquet", "OpeningCeremony"])
    def test_flat_phases(self, phase: str) -> None:
        assert classify_phase_type(phase) is PhaseType.FLAT

    def test_awards_phase(self) -> None:
        assert classify_phase_type("Awards") is PhaseType.AWARDS

    def test_none_defaults_to_nested(self) -> None:
        assert classify_phase_type(None) is PhaseType.NESTED

    def test_unknown_phase_defaults_to_nested(self) -> None:
        assert classify_phase_type("SomeFuturePhase") is PhaseType.NESTED


class TestSanitizeCollectionName:
    def test_none_becomes_random(self) -> None:
        assert sanitize_collection_name(None) == "Random"

    def test_empty_string_becomes_random(self) -> None:
        assert sanitize_collection_name("") == "Random"

    def test_whitespace_only_becomes_random(self) -> None:
        assert sanitize_collection_name("   ") == "Random"

    def test_trims_surrounding_whitespace(self) -> None:
        assert sanitize_collection_name("  Senior  ") == "Senior"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PodiumTraining", "Podium Training"),
            ("CompetitionWarmup", "Competition Warmup"),
            ("OpeningCeremony", "Opening Ceremony"),
            ("QualificationWarmup", "Qualification Warmup"),
        ],
    )
    def test_display_name_substitutions(self, raw: str, expected: str) -> None:
        assert sanitize_collection_name(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("A<B", "A_B"),
            ("A>B", "A_B"),
            ('A"B', "A_B"),
            ("A/B", "A_B"),
            ("A\\B", "A_B"),
            ("A|B", "A_B"),
            ("A?B", "A_B"),
            ("A*B", "A_B"),
            ("A:B", "A_B"),
        ],
    )
    def test_replaces_each_illegal_character(self, raw: str, expected: str) -> None:
        assert sanitize_collection_name(raw) == expected

    def test_keeps_internal_spaces(self) -> None:
        assert sanitize_collection_name("Great Britain") == "Great Britain"

    def test_does_not_apply_substitution_after_sanitizing(self) -> None:
        # Substitution keys are matched on the *trimmed* raw name, not a sanitized one.
        assert sanitize_collection_name(" PodiumTraining ") == "Podium Training"


class TestDeriveInternationalPathStopAtFirstMissing:
    def test_missing_phase_stops_immediately(self) -> None:
        segments = derive_international_path(
            {}, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Random"]

    def test_missing_age_stops_after_phase(self) -> None:
        metadata = {"Phase": "Qualification"}
        segments = derive_international_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Qualification", "Random"]

    def test_missing_nationality_stops_after_section(self) -> None:
        metadata = {"Phase": "Qualification", "Age": "Senior", "Section": "Women"}
        segments = derive_international_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Qualification",
            "Senior",
            "Women",
            "Random",
        ]

    def test_all_present_appends_apparatus(self) -> None:
        metadata = {
            "Phase": "Qualification",
            "Age": "Senior",
            "Section": "Women",
            "Nationality": "Great Britain",
            "Apparatus": "Floor",
        }
        segments = derive_international_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Qualification",
            "Senior",
            "Women",
            "Great Britain",
            "Floor",
        ]


class TestDeriveClubPath:
    def test_awards(self) -> None:
        metadata = {"AwardsSection": "Senior Women"}
        segments = derive_club_path(metadata, PhaseType.AWARDS, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Awards", "Senior Women"]

    def test_awards_missing_section_falls_back_to_random(self) -> None:
        segments = derive_club_path({}, PhaseType.AWARDS, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Awards", "Random"]

    def test_flat_includes_group_when_present(self) -> None:
        metadata = {"Phase": "Travelling", "Group": "Group A"}
        segments = derive_club_path(metadata, PhaseType.FLAT, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Travelling", "Group A"]

    def test_flat_missing_group_falls_back_to_random(self) -> None:
        metadata = {"Phase": "Travelling"}
        segments = derive_club_path(metadata, PhaseType.FLAT, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Travelling", "Random"]

    def test_missing_phase_stops_immediately(self) -> None:
        segments = derive_club_path({}, PhaseType.NESTED, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Random"]

    def test_missing_group_stops_after_phase(self) -> None:
        metadata = {"Phase": "Qualification"}
        segments = derive_club_path(metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Qualification", "Random"]

    def test_missing_team_name_stops_after_group(self) -> None:
        metadata = {"Phase": "Qualification", "Group": "Group A", "ClubName": "Stockholm"}
        segments = derive_club_path(metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Qualification", "Group A", "Random"]

    def test_missing_club_name_stops_after_group(self) -> None:
        metadata = {"Phase": "Qualification", "Group": "Group A", "TeamName": "Stars A"}
        segments = derive_club_path(metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME])
        assert segments == ["Competitions", COMPETITION_NAME, "Qualification", "Group A", "Random"]

    def test_illegal_characters_in_club_and_team_name_sanitized_after_combining(self) -> None:
        metadata = {
            "Phase": "Qualification",
            "Group": "Group A",
            "ClubName": "Stockholm/Gymnastics",
            "TeamName": "Stars: A",
            "Apparatus": "Floor",
        }
        segments = derive_club_path(metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME])
        assert segments[-2] == "Stockholm_Gymnastics - Stars_ A"


class TestDeriveInternationalClubPath:
    def test_awards(self) -> None:
        metadata = {"AwardsSection": "Junior Men"}
        segments = derive_international_club_path(
            metadata, PhaseType.AWARDS, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Awards", "Junior Men"]

    def test_flat_only_includes_phase(self) -> None:
        metadata = {"Phase": "Banquet"}
        segments = derive_international_club_path(
            metadata, PhaseType.FLAT, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Banquet"]

    def test_missing_phase_stops_immediately(self) -> None:
        segments = derive_international_club_path(
            {}, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Random"]

    def test_missing_age_stops_after_phase(self) -> None:
        metadata = {"Phase": "Competition"}
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Competition", "Random"]

    def test_missing_section_stops_after_age(self) -> None:
        metadata = {"Phase": "Competition", "Age": "Junior"}
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == ["Competitions", COMPETITION_NAME, "Competition", "Junior", "Random"]

    def test_missing_nationality_and_club_stops_after_section(self) -> None:
        metadata = {"Phase": "Competition", "Age": "Junior", "Section": "Women"}
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Competition",
            "Junior",
            "Women",
            "Random",
        ]

    def test_club_only_uses_club_name_alone(self) -> None:
        metadata = {
            "Phase": "Competition",
            "Age": "Junior",
            "Section": "Women",
            "ClubName": "Bracknell",
        }
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Competition",
            "Junior",
            "Women",
            "Bracknell",
            "Random",
        ]

    def test_nationality_only_uses_olympic_code_alone(self) -> None:
        metadata = {
            "Phase": "Competition",
            "Age": "Junior",
            "Section": "Women",
            "Nationality": "Great Britain",
        }
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Competition",
            "Junior",
            "Women",
            "GBR",
            "Random",
        ]

    def test_both_present_combines_olympic_code_and_club(self) -> None:
        metadata = {
            "Phase": "Competition",
            "Age": "Junior",
            "Section": "Women",
            "Nationality": "Great Britain",
            "ClubName": "Bracknell",
            "Apparatus": "Tumble",
        }
        segments = derive_international_club_path(
            metadata, PhaseType.NESTED, ["Competitions", COMPETITION_NAME]
        )
        assert segments == [
            "Competitions",
            COMPETITION_NAME,
            "Competition",
            "Junior",
            "Women",
            "GBR - Bracknell",
            "Tumble",
        ]


class TestDerivePathDispatch:
    def test_no_metadata_at_all_defaults_to_international_random(self) -> None:
        # No CompetitionType at all -> International default fallback, per
        # deriveCollectionPathFromPhoto's `else` branch, immediately stopping at Random since
        # Phase is also missing.
        path = derive_path({}, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Random"]

    def test_unrecognised_competition_type_defaults_to_international(self) -> None:
        metadata = {"CompetitionType": "SomeFutureType", "Phase": "Travelling"}

        path = derive_path(metadata, COMPETITION_NAME)

        assert path == ["Competitions", COMPETITION_NAME, "Travelling"]

    def test_competition_name_is_sanitized(self) -> None:
        path = derive_path({}, "Champs/2026")

        assert path[:2] == ["Competitions", "Champs_2026"]


class TestDeriveDestinationDir:
    def test_joins_dest_root_with_derived_segments(self, tmp_path: Path) -> None:
        metadata = {"CompetitionType": "International", "Phase": "Travelling"}

        result = derive_destination_dir(tmp_path, metadata, COMPETITION_NAME)

        assert result == tmp_path / "Competitions" / COMPETITION_NAME / "Travelling"

    def test_photo_with_no_metadata_lands_under_random(self, tmp_path: Path) -> None:
        result = derive_destination_dir(tmp_path, {}, COMPETITION_NAME)

        assert result == tmp_path / "Competitions" / COMPETITION_NAME / "Random"
