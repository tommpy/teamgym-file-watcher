# Testing rules

- pytest only. Every step ships tests with the code; `uv run pytest -q` must pass before commit.
- No network: mock the API with `pytest-httpx`. No sleeps: inject clocks/timeouts.
- Filesystem tests use `tmp_path`; create fake JPEG/RAW files as needed (a helper fixture
  builds tiny valid JPEGs with controlled EXIF). The real Lightroom JPEGs in `samples/` are
  fixtures for EXIF extraction tests.
- Port the Lua test cases: `MetadataMapperTests.lua` and `PhotoCollectionHierarchyTests.lua`
  in `../lightroom-plugins/teamgym.lrdevplugin/` become parametrised pytest cases with the
  same inputs and expected outputs. Cite the Lua test name in the pytest id.
- Coverage: aim ≥ 90% on `mapping.py`, `hierarchy.py`, `matcher.py`; enforce with
  `pytest-cov` fail-under on those modules once they exist.
- Integration test (late step): fake API via pytest-httpx + tmp dirs, run `process-once`
  end-to-end, assert final tree layout and journal contents.
- Tests must pass on Windows and macOS: no path-separator assumptions, no symlinks, beware
  case-insensitive collisions.
