# Code review — step 06 (PhotoCollectionHierarchy port)

Reviewed commit `2e37cfb` (`feat: port phase-based collection hierarchy with exact Lua
parity`) against `../lightroom-plugins/teamgym.lrdevplugin/PhotoCollectionHierarchy.lua` and
`PhotoCollectionHierarchyTests.lua`.

## Findings

None. Line-by-line comparison against the Lua found no behavioural divergence:

- `classify_phase_type`/`classifyPhaseType`: identical NESTED/FLAT/AWARDS sets, `None`/unknown
  phase both default to Nested.
- `sanitize_collection_name`/`sanitizeCollectionName`: same trim → display-substitution →
  illegal-character-replacement → empty-becomes-"Random" pipeline, same four substitutions,
  same `<>:"/\|?*` character class. The Lua's `sanitizeCollectionName(...) or "Random"` call-site
  suffixes are correctly identified as dead code (the function never returns nil/""), and the
  Python omits them — the docstring calls this out explicitly rather than silently diverging.
- `derive_international_path`, `derive_club_path`, `derive_international_club_path`: branch
  order (Awards/Flat/Nested) and every nested stop-at-first-missing-level early return matches
  the Lua exactly, including the InternationalClub partial-fallback precedence (both →
  Olympic-code-and-club, club-only, nationality-only, neither) and the Club Awards-has-no-Group
  comment.
- `derive_path`/`deriveCollectionPathFromPhoto`: dispatches on `CompetitionType` with
  International as the fallback for both an explicit `"International"` value and a
  missing/unrecognised one, same as the Lua's `else` branch.
- `derive_destination_dir` is a Python-only addition (no on-disk equivalent needed in the Lua,
  which manipulates Lightroom collections) — correctly scoped and documented as new rather than
  ported.
- The Lua's "By Team" hierarchy is deliberately not ported (this watcher moves each physical
  file to one place; there's no second on-disk tree to build), matching prompts/06's explicit
  scope. Not a `docs/deviations.md` case — no Lua behaviour is reproduced incorrectly, a whole
  parallel feature is out of scope by design, and this is documented in the module docstring.
- Tests: all nine phase-based cases from `PhotoCollectionHierarchyTests.lua` ported and cited
  by Lua name (`testInternational*`, `testClub*`, `testInternationalClub*`); the two "By Team"
  tests and the two catalog-level mixed-selection tests are correctly out of scope. Additional
  stop-at-first-missing-level cases for all three competition types, every illegal character,
  and the no-metadata-at-all → International/Random fallback are all covered. No network, no
  filesystem, no sleeps/ordering dependencies.
- `metadata: dict[str, str]` mirrors the `dict[str, str]` convention already established (and
  reviewed clean) in `mapping.py`'s `MapResult.metadata` — consistent with the existing
  "no key = unset" mirror of Lua's plain tables, not a new violation of the python.md
  no-dict-shaped-data rule.

## Verification

```
uv run ruff check .        # All checks passed
uv run mypy src/           # Success: no issues found in 9 source files
uv run pytest -q           # 324 passed, 1 skipped
```

`hierarchy.py` coverage: 100% (129/129 statements).

No fixes required; nothing committed beyond this review file.
