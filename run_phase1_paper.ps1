# ============================================================
# MiniQuantDesk – Phase 1 Paper Runner
# Purpose:
#   - Load local env vars
#   - Start paper trading using CONFIG-DRIVEN cadence
#   - NO overrides of runtime logic or config
#
# Usage:
#   Right-click → Run with PowerShell
#   or
#   ./run_phase1_paper.ps1
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== MiniQuantDesk :: Phase 1 Paper Launch ===" -ForegroundColor Cyan

# ------------------------------------------------------------
# Step 1: Move to repo root (this file lives in repo root)
# ------------------------------------------------------------
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot
Write-Host "[ok] Repo root: $RepoRoot" -ForegroundColor Green

# ------------------------------------------------------------
# Step 2: Load config/.env.local into process env
# ------------------------------------------------------------
$EnvFile = Join-Path $RepoRoot "config\.env.local"

if (-not (Test-Path $EnvFile)) {
    Write-Host "[ERROR] Missing config\.env.local" -ForegroundColor Red
    exit 1
}

Write-Host "[info] Loading environment variables from config\.env.local"

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }

    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) { return }

    $key = $parts[0].Trim()
    $val = $parts[1].Trim().Trim('"').Trim("'")

    [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
}

Write-Host "[ok] Environment loaded" -ForegroundColor Green

# ------------------------------------------------------------
# Step 3: Launch Phase 1 paper runtime
# IMPORTANT:
#   --interval 0 means:
#     → Use config.yaml session.cycle_interval_seconds
#     → Closed / pre-open cadence comes from config + env
# ------------------------------------------------------------
Write-Host ""
Write-Host "[launch] Starting Phase 1 paper runtime" -ForegroundColor Yellow
Write-Host "         Cadence source: config/config.yaml (session.*)" -ForegroundColor DarkGray
Write-Host "         Press Ctrl+C to stop cleanly" -ForegroundColor DarkGray
Write-Host ""

python entry_paper.py --interval 0

# ------------------------------------------------------------
# Step 4: Clean exit banner
# ------------------------------------------------------------
Write-Host ""
Write-Host "[exit] Phase 1 paper runtime stopped" -ForegroundColor Cyan
