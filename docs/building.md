# Building this repo (agentic pipeline)

This app was built step-by-step by an automated Claude Code pipeline, driven by the
numbered prompts in `prompts/` against the design in [`PLAN.md`](../PLAN.md). This document
is for anyone re-running or extending that pipeline; it is not needed to *use* the finished
app (see [`README.md`](../README.md) for that).

## Prerequisites

[Claude Code](https://docs.claude.com/en/docs/claude-code) CLI logged in, `uv`, `git`.

## Running the pipeline

Resumable; safe to re-run after a failure.

```powershell
# Windows
.\scripts\run_build.ps1
# start from a specific step:
.\scripts\run_build.ps1 -StartStep 5
```

```bash
# macOS
./scripts/run_build.sh          # or: ./scripts/run_build.sh 5
```

Each step:

1. Sonnet executes the numbered prompt (`prompts/NN_*.md`).
2. Sonnet code-reviews its own work (`prompts/tasks/code_review.md`) against Lua parity,
   file-immutability, safety, cross-platform, test-quality, and standards checks; fixes are
   committed separately as `fix(review): …`.
3. `uv run pytest` must pass and the tree must be clean and committed, or the runner fails
   the step.
4. Progress is recorded in `.build_progress` (git-ignored) so the runner can resume from the
   last completed step.

Each step ends with its own commit; see `git log` for the full history of what each step
delivered.

## Repo layout (build scaffold)

```
PLAN.md               Design doc — read first
CLAUDE.md             Instructions for the build agents
prompts/NN_*.md        Sequential build steps
prompts/rules/        Coding standards referenced by every step
prompts/tasks/         Code review prompt
scripts/run_build.*    Unattended build runner
samples/               Real Lightroom-written JPEGs used as EXIF test fixtures
docs/code_reviews/     Per-step review summaries written by the review pass
```

## After the build

Create the GitHub repo and push:

```bash
git remote add origin git@github.com:<you>/teamgym-file-watcher.git
git push -u origin main
```
