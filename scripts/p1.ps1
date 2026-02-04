Write-Host "Running P0..." -ForegroundColor Cyan
python -m pytest -q `
  tests/patch2 `
  tests/patch3 `
  tests/acceptance/test_phase1_execution_integrity.py `
  tests/test_smoke.py `
  tests/test_system_acceptance.py `
  tests/test_integration_simple.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Running P1..." -ForegroundColor Cyan
python -m pytest -q tests/p1
exit $LASTEXITCODE
