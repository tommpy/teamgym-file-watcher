# Task: Code review of the latest step

Review the most recent commit(s) since the previous step (`git log --oneline -5`,
`git show HEAD` / `git diff HEAD~1`). You are the quality gate for code written by another
agent. Be tough but act only on real issues.

Check, in order of importance:

1. **Lua parity** (if this step touched `mapping.py`, `hierarchy.py`, or `matcher.py`):
   diff the logic line-by-line against the corresponding Lua in
   `../lightroom-plugins/teamgym.lrdevplugin/`. Any behavioural divergence — enum values,
   Random fallback order, sanitisation, composite segment formats, inclusive time bounds,
   first-match-wins — is a must-fix.
2. **File immutability**: photos must never be modified — the watcher only reads and moves.
   Flag anything that opens a photo for writing.
3. **Safety**: any code path that could overwrite, delete, or lose a photo; unhandled
   per-photo exceptions that could kill the watcher loop; journal written before/after the
   move correctly.
4. **Cross-platform**: path handling, case-insensitivity, atomic-move fallback, no
   POSIX-isms.
5. **Tests**: meaningful assertions (not tautologies), Lua cases actually ported, no network,
   no flakiness (sleeps, ordering, real clocks).
6. **Standards**: `prompts/rules/python.md` compliance; ruff/mypy clean; no dead code.

For every must-fix issue: fix it, add/adjust a test that would have caught it, and re-run
`uv run ruff check . && uv run mypy src/ && uv run pytest -q`.
Commit fixes as `fix(review): <summary>` (one commit; skip committing if nothing to fix).
Write a short review summary to `docs/code_reviews/<step>.md` (create dirs as needed) listing
issues found and their resolution, then commit that with the same commit (or a `docs:` commit
if there were no fixes).
