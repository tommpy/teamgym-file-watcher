#!/usr/bin/env bash
# run_build.sh — unattended build pipeline (macOS/Linux mirror of run_build.ps1)
# Usage: ./scripts/run_build.sh [start_step] [model]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PROGRESS_FILE=".build_progress"
MODEL="${2:-sonnet}"
MAX_FIX_ROUNDS=2
CLAUDE_ARGS=(--model "$MODEL" --permission-mode acceptEdits --output-format stream-json --verbose)

# Project-scoped permissions
if [[ ! -f .claude/settings.json ]]; then
  mkdir -p .claude
  cp scripts/claude-settings.json .claude/settings.json
  echo "Installed .claude/settings.json permissions"
fi

last_completed() { [[ -f "$PROGRESS_FILE" ]] && cat "$PROGRESS_FILE" || echo 0; }

run_claude() { # $1=prompt-text $2=label
  echo -e "\n=== $2 ($MODEL) ==="
  # stream-json emits one JSON event per line as the agent works; render live.
  # pipefail (set at top) makes this fail if either claude or the filter fails.
  claude -p "${CLAUDE_ARGS[@]}" <<< "$1" | python3 -u "$REPO_ROOT/scripts/stream_filter.py"
}

test_gate() { uv run pytest -q; }

assert_clean() { # $1=label
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Tree dirty after $1 — agent did not commit" >&2; exit 1
  fi
}

START_STEP="${1:-0}"
[[ "$START_STEP" == "0" ]] && START_STEP=$(( $(last_completed) + 1 ))
echo "Starting at step $START_STEP"

for p in prompts/[0-9][0-9]_*.md; do
  step=$((10#$(basename "$p" | cut -c1-2)))
  (( step < START_STEP )) && continue

  head_before=$(git rev-parse HEAD 2>/dev/null || echo none)

  run_claude "$(cat "$p")" "Step $step: $(basename "$p")"

  round=0
  until test_gate; do
    round=$((round + 1))
    (( round > MAX_FIX_ROUNDS )) && { echo "Step $step: tests failing after $MAX_FIX_ROUNDS fix rounds" >&2; exit 1; }
    echo "Tests failing — fix round $round"
    run_claude "The test suite is failing after completing prompts/$(basename "$p"). Run 'uv run pytest -q', diagnose, fix properly (no test deletion/skipping to pass), re-run until green, then commit the fix per prompts/rules/git.md." "Step $step fix round $round"
  done

  assert_clean "step $step"
  [[ "$(git rev-parse HEAD)" == "$head_before" ]] && { echo "Step $step made no commit" >&2; exit 1; }

  run_claude "$(cat prompts/tasks/code_review.md)

Context: you are reviewing the work for prompts/$(basename "$p") (step $step)." "Step $step review"

  test_gate || { echo "Step $step: tests failing after review pass" >&2; exit 1; }
  assert_clean "step $step review"

  echo "$step" > "$PROGRESS_FILE"
  echo "=== Step $step complete ==="
done

echo -e "\nAll steps complete. Review 'git log', then add your GitHub remote and push."
