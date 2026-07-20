# run_build.ps1 — unattended build pipeline for teamgym-file-watcher
# Feeds each prompts/NN_*.md to Claude Code (Sonnet), runs a review pass, gates on
# tests + a clean committed tree, records progress in .build_progress. Resumable.
#
# Usage:  .\scripts\run_build.ps1 [-StartStep 1] [-Model sonnet] [-MaxFixRounds 2]

param(
    [int]$StartStep = 0,          # 0 = resume from .build_progress
    [string]$Model = "sonnet",
    [int]$MaxFixRounds = 2
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$ProgressFile = Join-Path $RepoRoot ".build_progress"
$ClaudeArgs = @("--model", $Model, "--permission-mode", "acceptEdits",
                "--output-format", "stream-json", "--verbose")

# Ensure project-scoped permissions are in place (Bash allowlist for uv/git/etc.)
$SettingsDir = Join-Path $RepoRoot ".claude"
if (-not (Test-Path (Join-Path $SettingsDir "settings.json"))) {
    New-Item -ItemType Directory -Force -Path $SettingsDir | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "claude-settings.json") (Join-Path $SettingsDir "settings.json")
    Write-Host "Installed .claude/settings.json permissions" -ForegroundColor Yellow
}

function Get-LastCompleted {
    if (Test-Path $ProgressFile) { return [int](Get-Content $ProgressFile -Raw).Trim() }
    return 0
}

function Invoke-Claude([string]$PromptText, [string]$Label) {
    Write-Host "`n=== $Label ($Model) ===" -ForegroundColor Cyan
    $isError = $false
    # stream-json emits one JSON event per line as the agent works; render live.
    $PromptText | claude -p @ClaudeArgs | ForEach-Object {
        $evt = $null
        try { $evt = $_ | ConvertFrom-Json } catch { Write-Host $_; return }
        switch ($evt.type) {
            "assistant" {
                foreach ($blk in $evt.message.content) {
                    if ($blk.type -eq "text" -and $blk.text) {
                        Write-Host $blk.text
                    } elseif ($blk.type -eq "tool_use") {
                        $hint = $blk.input.file_path
                        if (-not $hint) { $hint = $blk.input.command }
                        if (-not $hint) { $hint = $blk.input.pattern }
                        $hint = ("$hint" -replace "`n", " ")
                        if ($hint.Length -gt 100) { $hint = $hint.Substring(0, 100) + "…" }
                        Write-Host "  [tool] $($blk.name) $hint" -ForegroundColor DarkGray
                    }
                }
            }
            "result" {
                $cost = if ($evt.total_cost_usd) { $evt.total_cost_usd } else { 0 }
                $durMs = if ($evt.duration_ms) { $evt.duration_ms } else { 0 }
                $durS = [math]::Round($durMs / 1000)
                Write-Host ("  [result] turns={0} cost=`${1:N2} duration={2}s" -f $evt.num_turns, $cost, $durS) -ForegroundColor DarkCyan
                if ($evt.is_error) { $isError = $true }
            }
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "claude exited $LASTEXITCODE during: $Label" }
    if ($isError) { throw "claude reported an error result during: $Label" }
}

function Test-Gate {
    uv run pytest -q
    return ($LASTEXITCODE -eq 0)
}

function Assert-CleanCommitted([string]$Label) {
    $dirty = git status --porcelain
    if ($dirty) { throw "Tree dirty after $Label — agent did not commit:`n$dirty" }
}

$prompts = Get-ChildItem (Join-Path $RepoRoot "prompts") -Filter "*.md" |
    Where-Object { $_.Name -match "^\d{2}_" } | Sort-Object Name

if ($StartStep -eq 0) { $StartStep = (Get-LastCompleted) + 1 }
Write-Host "Starting at step $StartStep of $($prompts.Count)" -ForegroundColor Green

foreach ($p in $prompts) {
    $stepNum = [int]($p.Name.Substring(0, 2))
    if ($stepNum -lt $StartStep) { continue }

    $headBefore = git rev-parse HEAD 2>$null

    # 1. Execute the step
    Invoke-Claude (Get-Content $p.FullName -Raw) "Step $stepNum : $($p.Name)"

    # 2. Test gate with bounded fix rounds
    $round = 0
    while (-not (Test-Gate)) {
        $round++
        if ($round -gt $MaxFixRounds) { throw "Step $stepNum : tests still failing after $MaxFixRounds fix rounds" }
        Write-Host "Tests failing — fix round $round" -ForegroundColor Yellow
        Invoke-Claude "The test suite is failing after completing prompts/$($p.Name). Run 'uv run pytest -q', diagnose, fix properly (no test deletion/skipping to pass), re-run until green, then commit the fix per prompts/rules/git.md." "Step $stepNum fix round $round"
    }

    # 3. Commit gate
    Assert-CleanCommitted "step $stepNum"
    $headAfter = git rev-parse HEAD
    if ($headAfter -eq $headBefore) { throw "Step $stepNum made no commit" }

    # 4. Review pass (skips itself if nothing to fix)
    $review = Get-Content (Join-Path $RepoRoot "prompts\tasks\code_review.md") -Raw
    Invoke-Claude "$review`n`nContext: you are reviewing the work for prompts/$($p.Name) (step $stepNum)." "Step $stepNum review"

    if (-not (Test-Gate)) { throw "Step $stepNum : tests failing after review pass" }
    Assert-CleanCommitted "step $stepNum review"

    # 5. Record progress
    Set-Content $ProgressFile $stepNum
    Write-Host "=== Step $stepNum complete ===" -ForegroundColor Green
}

Write-Host "`nAll steps complete. Review 'git log', then add your GitHub remote and push." -ForegroundColor Green
