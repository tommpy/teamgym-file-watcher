# Step 05 — Port MetadataMapper (Lua → Python)

Read, in full, `../lightroom-plugins/teamgym.lrdevplugin/MetadataMapper.lua` and
`MetadataMapperTests.lua`, plus `MyMetadataDefinitionFile.lua` (canonical field ids and enum
values). Parity is the entire point of this step — see CLAUDE.md.

## Goal
`mapping.py`: translate a `Tag` into the plugin's metadata fields with identical values.

## Requirements
1. Port verbatim (same keys, same values, including aliases):
   `SECTION_MAPPING`, `AGE_GROUP_MAPPING`, `APPARATUS_MAPPING`, `PHASE_MAPPING`
   (note FINALS→Competition, FINALS_WARMUP→CompetitionWarmup, WARM_UP legacy),
   `COMPETITION_TYPE_MAPPING`, `NATIONALITY_MAPPING` (all IOC + alternative ISO aliases).
2. `map_tag_to_metadata(tag) -> MapResult` where `MapResult` has
   `metadata: dict[str, str]` (keys are the plugin field ids: Section, Age, Competition,
   Apparatus, Phase, ClubName, TeamName, Nationality, CompetitionType, AwardsSection, Group,
   RoundType) and `warnings: list[str]`. Reproduce the Lua semantics exactly:
   - unmapped section/age/apparatus/phase/competition-type → warning, field omitted;
   - unknown nationality code → **used as-is** plus warning (unlike the others!);
   - ClubName only when present and non-empty; TeamName whenever present;
   - Award → AwardsSection from `award.name`; Group from `group.name`; RoundType from
     `group.roundType`.
3. `nationality_to_olympic_code(country_name)` — port including the explicit preferred-code
   overrides (Bulgaria→BUL, Denmark→DEN, Germany→GER, Latvia→LAT, Netherlands→NED,
   Portugal→POR, Romania→ROU, Slovenia→SVN, Switzerland→SUI) and pass-through-with-warning
   for unknown names.
4. First-match-wins helper mirroring `mapTagsToMetadata` (API guarantees non-overlap).
5. Tests: port every case from `MetadataMapperTests.lua` (cite Lua test names in ids); add
   full round-trip table tests for every enum value in `MyMetadataDefinitionFile.lua`; cover
   every alias code and both directions of the olympic-code map.

## Definition of done
Checks pass, coverage ≥ 90 % on `mapping.py`; commit
`feat: port MetadataMapper with exact Lua parity`.
