# Step 06 — Port PhotoCollectionHierarchy path derivation (Lua → Python)

Read, in full, `../lightroom-plugins/teamgym.lrdevplugin/PhotoCollectionHierarchy.lua` and
`PhotoCollectionHierarchyTests.lua`. Only the **phase-based** hierarchy is needed (no
"By Team" — photos live in one physical place).

## Goal
`hierarchy.py`: metadata → relative folder path segments, byte-identical to what the plugin
derives for its collections.

## Requirements
1. Port exactly: `classifyPhaseType` (NESTED / FLAT / AWARDS sets, default Nested),
   `sanitizeCollectionName` (trim; display substitutions PodiumTraining→"Podium Training",
   CompetitionWarmup→"Competition Warmup", OpeningCeremony→"Opening Ceremony",
   QualificationWarmup→"Qualification Warmup"; replace `<>:"/\|?*` with `_`; empty/None →
   "Random"), and the three derivers:
   - `deriveInternationalPath`: Nested = Phase→Age→Section→**full Nationality name**→
     Apparatus; Flat = Phase; Awards = Awards→AwardsSection|Random. Stop-at-first-missing →
     append "Random" once and stop (replicate the Lua's exact early-return pattern).
   - `deriveClubPath`: Nested = Phase→Group→"ClubName - TeamName"→Apparatus; Flat =
     Phase→Group|Random; Awards = Awards→AwardsSection|Random.
   - `deriveInternationalClubPath`: Nested = Phase→Age→Section→"OlympicCode - ClubName"
     (with the partial-fallbacks when only one of nationality/club present, using
     `nationality_to_olympic_code`)→Apparatus; Flat = Phase; Awards as above.
2. Entry point `derive_path(metadata: dict[str, str], competition_name: str) ->
   PurePosixPath`-style segment list starting `["Competitions", <sanitised competition>]`,
   dispatching on CompetitionType with International as the default fallback — exactly as
   `deriveCollectionPathFromPhoto`.
3. On disk this maps to `dest_root / Path(*segments)`. Add a helper for that; also handle a
   photo with NO metadata at all (no CompetitionType) → per Lua default path International →
   ".../Random" — write a test proving where such files land.
4. Tests: port every case from `PhotoCollectionHierarchyTests.lua` (cite names); add cases
   for each stop-at-first-missing level per competition type, sanitisation of illegal
   characters in club/team/award names, and the composite-segment edge cases.

## Definition of done
Checks pass, coverage ≥ 90 % on `hierarchy.py`; commit
`feat: port phase-based collection hierarchy with exact Lua parity`.
