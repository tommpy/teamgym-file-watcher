"""Port of ``PhotoCollectionHierarchy.lua``'s phase-based path derivation.

Only the phase-based hierarchy is ported (per CLAUDE.md / prompts/06): the plugin's "By Team"
hierarchy duplicates photos into a second collection tree inside Lightroom, but this watcher
only moves each physical file into one place on disk, so there is no on-disk equivalent to
derive.

Parity notes:

- ``sanitizeCollectionName`` in the Lua always returns a truthy string (never ``nil``/``""``),
  so its call sites' ``... or "Random"`` suffixes are dead code — the real "stop at first
  missing level" behaviour comes from the separate ``if not photoMetadata.X then return end``
  guards, which this module reproduces via the early ``return`` after each ``.append`` below.
- ``metadata`` uses plain ``dict`` lookups (:meth:`dict.get` returning ``None``) to mirror Lua's
  ``nil`` for an absent table key, exactly as :mod:`teamgym_watcher.mapping` produces it.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Final

from teamgym_watcher.mapping import nationality_to_olympic_code

if TYPE_CHECKING:
    from pathlib import Path


class PhaseType(Enum):
    """Mirrors the Lua's ``PHASE_TYPES`` table."""

    NESTED = "Nested"
    FLAT = "Flat"
    AWARDS = "Awards"


_NESTED_PHASES: Final[frozenset[str]] = frozenset(
    {
        "PodiumTraining",
        "PodiumTrainingWarmup",
        "QualificationWarmup",  # INTERNATIONAL-only, mirrors Competition/CompetitionWarmup pairing
        "Qualification",
        "Competition",
        "CompetitionWarmup",
    }
)

_FLAT_PHASES: Final[frozenset[str]] = frozenset({"Travelling", "Banquet", "OpeningCeremony"})

_AWARDS_PHASES: Final[frozenset[str]] = frozenset({"Awards"})

_DISPLAY_NAME_SUBSTITUTIONS: Final[dict[str, str]] = {
    "PodiumTraining": "Podium Training",
    "CompetitionWarmup": "Competition Warmup",
    "PodiumTrainingWarmup": "Podium Training Warmup",
    "OpeningCeremony": "Opening Ceremony",
    "QualificationWarmup": "Qualification Warmup",
}

_ILLEGAL_CHARS: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*]')


def classify_phase_type(phase: str | None) -> PhaseType:
    """Port of ``classifyPhaseType``: unknown/missing phases default to Nested."""
    if phase is None:
        return PhaseType.NESTED
    if phase in _NESTED_PHASES:
        return PhaseType.NESTED
    if phase in _FLAT_PHASES:
        return PhaseType.FLAT
    if phase in _AWARDS_PHASES:
        return PhaseType.AWARDS
    return PhaseType.NESTED


def sanitize_collection_name(name: str | None) -> str:
    """Port of ``sanitizeCollectionName``: trim, apply display substitutions, replace illegal
    filesystem characters with ``_``, and fall back to "Random" for ``None``/empty input.
    """
    if name is None:
        return "Random"

    trimmed = name.strip()
    trimmed = _DISPLAY_NAME_SUBSTITUTIONS.get(trimmed, trimmed)
    sanitized = _ILLEGAL_CHARS.sub("_", trimmed)

    if sanitized == "":
        return "Random"
    return sanitized


def derive_international_path(
    metadata: dict[str, str], phase_type: PhaseType, segments: list[str]
) -> list[str]:
    """Port of ``deriveInternationalPath``.

    Nested = Phase -> Age -> Section -> full Nationality name -> Apparatus (the nationality
    level IS the team for INTERNATIONAL competitions, so the full country name is used
    directly, unlike ``deriveInternationalClubPath``'s Olympic-code composite).
    """
    if phase_type is PhaseType.FLAT:
        segments.append(sanitize_collection_name(metadata.get("Phase")))
        return segments

    if phase_type is PhaseType.AWARDS:
        segments.append("Awards")
        segments.append(sanitize_collection_name(metadata.get("AwardsSection")))
        return segments

    phase = metadata.get("Phase")
    segments.append(sanitize_collection_name(phase))
    if phase is None:
        return segments

    age = metadata.get("Age")
    segments.append(sanitize_collection_name(age))
    if age is None:
        return segments

    section = metadata.get("Section")
    segments.append(sanitize_collection_name(section))
    if section is None:
        return segments

    nationality = metadata.get("Nationality")
    segments.append(sanitize_collection_name(nationality))
    if nationality is None:
        return segments

    segments.append(sanitize_collection_name(metadata.get("Apparatus")))
    return segments


def derive_club_path(
    metadata: dict[str, str], phase_type: PhaseType, segments: list[str]
) -> list[str]:
    """Port of ``deriveClubPath``: Nested = Phase -> Group -> "ClubName - TeamName" -> Apparatus."""
    if phase_type is PhaseType.AWARDS:
        # Awards phase has no Group in the API (group is null for awards tags).
        segments.append("Awards")
        segments.append(sanitize_collection_name(metadata.get("AwardsSection")))
        return segments

    if phase_type is PhaseType.FLAT:
        segments.append(sanitize_collection_name(metadata.get("Phase")))
        segments.append(sanitize_collection_name(metadata.get("Group")))
        return segments

    phase = metadata.get("Phase")
    segments.append(sanitize_collection_name(phase))
    if phase is None:
        return segments

    group = metadata.get("Group")
    segments.append(sanitize_collection_name(group))
    if group is None:
        return segments

    club_name = metadata.get("ClubName")
    team_name = metadata.get("TeamName")
    team_identity = (
        f"{club_name} - {team_name}" if club_name is not None and team_name is not None else None
    )
    segments.append(sanitize_collection_name(team_identity))
    if team_identity is None:
        return segments

    segments.append(sanitize_collection_name(metadata.get("Apparatus")))
    return segments


def derive_international_club_path(
    metadata: dict[str, str], phase_type: PhaseType, segments: list[str]
) -> list[str]:
    """Port of ``deriveInternationalClubPath``.

    Nested = Phase -> Age -> Section -> "OlympicCode - ClubName" -> Apparatus, with partial
    fallbacks (club-only, or Olympic-code-only) when just one of nationality/club is present.
    """
    if phase_type is PhaseType.AWARDS:
        # Awards phase has no Age, Section, Team, or Apparatus.
        segments.append("Awards")
        segments.append(sanitize_collection_name(metadata.get("AwardsSection")))
        return segments

    if phase_type is PhaseType.FLAT:
        segments.append(sanitize_collection_name(metadata.get("Phase")))
        return segments

    phase = metadata.get("Phase")
    segments.append(sanitize_collection_name(phase))
    if phase is None:
        return segments

    age = metadata.get("Age")
    segments.append(sanitize_collection_name(age))
    if age is None:
        return segments

    section = metadata.get("Section")
    segments.append(sanitize_collection_name(section))
    if section is None:
        return segments

    nationality = metadata.get("Nationality")
    club_name = metadata.get("ClubName")
    nationality_club: str | None
    if nationality is not None and club_name is not None:
        nationality_club = f"{nationality_to_olympic_code(nationality)} - {club_name}"
    elif club_name is not None:
        nationality_club = club_name
    elif nationality is not None:
        nationality_club = nationality_to_olympic_code(nationality)
    else:
        nationality_club = None

    segments.append(sanitize_collection_name(nationality_club))
    if nationality_club is None:
        return segments

    segments.append(sanitize_collection_name(metadata.get("Apparatus")))
    return segments


def derive_path(metadata: dict[str, str], competition_name: str) -> list[str]:
    """Port of ``deriveCollectionPathFromPhoto``.

    Dispatches on ``CompetitionType``; International is the default fallback for both an
    explicit ``"International"`` value and a missing/unrecognised one (e.g. a photo with no
    metadata at all), exactly as the Lua's ``else`` branch does.
    """
    phase_type = classify_phase_type(metadata.get("Phase"))
    segments = ["Competitions", sanitize_collection_name(competition_name)]

    competition_type = metadata.get("CompetitionType")
    if competition_type == "Club":
        return derive_club_path(metadata, phase_type, segments)
    if competition_type == "InternationalClub":
        return derive_international_club_path(metadata, phase_type, segments)
    return derive_international_path(metadata, phase_type, segments)


def derive_destination_dir(
    dest_root: Path, metadata: dict[str, str], competition_name: str
) -> Path:
    """Resolve the on-disk directory for a photo: ``dest_root / Path(*derive_path(...))``."""
    return dest_root.joinpath(*derive_path(metadata, competition_name))
