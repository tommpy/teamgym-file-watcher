# teamgym-file-watcher

Watches an import directory for competition photos, matches them to TeamGym competition
tags from the teamgym-sorter API by capture time, and moves them into a Zenfolio-ready
folder hierarchy on disk. Files are never modified — the API remains the metadata source of
truth, and Lightroom re-tags from it on import as today. See [PLAN.md](PLAN.md) for the
full design.

## Status

Planning scaffold. The application is built step-by-step by an automated Claude Code pipeline.

## Building the app (automated)

Prerequisites: [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI logged in,
`uv`, `git`.

1. Run the pipeline (resumable; safe to re-run after a failure):

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

Each step: Sonnet executes the prompt → Sonnet code-reviews (`prompts/tasks/code_review.md`)
→ tests must pass → tree must be clean and committed → progress recorded in `.build_progress`.

2. After the build, create the GitHub repo and push:

   ```bash
   git remote add origin git@github.com:<you>/teamgym-file-watcher.git
   git push -u origin main
   ```

## Using the app (once built)

```bash
uv run teamgym-watcher auth                 # one-time GitHub device flow
uv run teamgym-watcher run --config teamgym-watcher.toml     # watch forever
uv run teamgym-watcher process-once --config teamgym-watcher.toml
uv run teamgym-watcher run --dry-run ...    # log everything, touch nothing
```

## Repo layout (scaffold)

```
PLAN.md               Design doc — read first
CLAUDE.md             Instructions for the build agents
prompts/NN_*.md       Sequential build steps
prompts/rules/        Coding standards referenced by every step
prompts/tasks/        Code review prompt
scripts/run_build.*   Unattended build runner
samples/              Real Lightroom-written JPEGs used as EXIF test fixtures
```
