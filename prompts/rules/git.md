# Git rules

- Work directly on `main`. Never push.
- One commit per prompt step, at the end, after lint + types + tests all pass:
  `git add -A && git commit -m "<type>: <summary>" -m "<body>"`.
- Conventional commits: `feat:`, `fix:`, `test:`, `chore:`, `docs:`, `refactor:`.
  Body: what the step delivered and any deviations/decisions.
- Review-pass fixes: separate `fix(review): …` commit (do not amend).
- Never commit: `.venv/`, `__pycache__/`, `.build_progress`, secrets/tokens, large binaries.
  `samples/` fixtures ARE committed (they are the spec).
- Leave the tree clean. The runner fails the step if `git status --porcelain` is non-empty
  or no new commit exists.
