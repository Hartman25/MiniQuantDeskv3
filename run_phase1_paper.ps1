$ErrorActionPreference = "Stop"

Write-Host "=== MiniQuantDesk Phase 1 Startup ===" -ForegroundColor Cyan
Set-Location "C:\Users\Zacha\Desktop\2"

Write-Host ""
Write-Host "[1/5] Running pytest..." -ForegroundColor Yellow
python -m pytest -q
Write-Host "OK: tests passed" -ForegroundColor Green

Write-Host ""
Write-Host "[2/5] Verifying entry_paper import safety..." -ForegroundColor Yellow
$importCheck = python -c "import entry_paper; print('import ok')"
if ($importCheck.Trim() -ne "import ok") { throw "Import produced unexpected output: $importCheck" }
Write-Host "OK: import clean" -ForegroundColor Green

Write-Host ""
Write-Host "[3/5] Environment check..." -ForegroundColor Yellow
python entry_paper.py --env-check
Write-Host "OK: env check complete" -ForegroundColor Green

Write-Host ""
Write-Host "[4/5] Smoke test (single cycle)..." -ForegroundColor Yellow
python entry_paper.py --once
Write-Host "OK: smoke test complete" -ForegroundColor Green

Write-Host ""
Write-Host "[5/5] Launching Phase 1 paper runner..." -ForegroundColor Yellow

$env:MQD_CLOSED_SLEEP_S          = "120"
$env:MQD_PREOPEN_WINDOW_M       = "10"
$env:MQD_PREOPEN_SLEEP_S        = "20"
$env:MARKET_CLOCK_CACHE_S       = "15"
$env:MQD_FAIL_OPEN_MARKET_HOURS = "0"
$env:HEARTBEAT_PRINT            = "1"

Write-Host "Running continuously. Ctrl+C to stop." -ForegroundColor Cyan
python entry_paper.py --interval 60
