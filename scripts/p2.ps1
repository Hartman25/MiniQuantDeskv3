# scripts/p2.ps1
# Phase 2 Gate: runs P0, then P1, then Phase 2 tests.
# Usage: ./scripts/p2.ps1

$ErrorActionPreference = "Stop"

function Run-Step {
    param(
        [Parameter(Mandatory=$true)][string]$Label,
        [Parameter(Mandatory=$true)][string]$Command
    )

    Write-Host ""
    Write-Host ("=== " + $Label + " ===") -ForegroundColor Cyan

    # Use cmd /c to avoid PowerShell quoting edge cases when invoking python/pytest
    cmd /c $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("FAIL: " + $Label + " (exit " + $LASTEXITCODE + ")") -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# Run P0 (Phase 1 baseline)
Run-Step "Running P0" "powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\p0.ps1"

# Run P1 (includes P0 + P1 suites in your repo)
Run-Step "Running P1" "powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\p1.ps1"

# Run Phase 2 tests
Run-Step "Running Phase 2 tests" "python -m pytest tests\p2 -q"

Write-Host ""
Write-Host "=== All Phase 2 gates PASSED ===" -ForegroundColor Green
exit 0
