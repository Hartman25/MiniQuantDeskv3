# ============================================================================
# MiniQuantDesk v2 - SAFE Installation Script
# ============================================================================
# This script handles common Windows installation issues
# ============================================================================

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "MiniQuantDesk v2 - Safe Installation" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan

$ProjectRoot = "C:\Users\Zacha\Desktop\MiniQuantDeskv2"
Set-Location $ProjectRoot

# ============================================================================
# STEP 1: Activate Virtual Environment
# ============================================================================
Write-Host "`n[1/6] Activating virtual environment..." -ForegroundColor Yellow

$VenvPath = "$ProjectRoot\venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "  Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
}

& "$VenvPath\Scripts\Activate.ps1"
Write-Host "  OK: Virtual environment activated" -ForegroundColor Green

# ============================================================================
# STEP 2: Upgrade pip
# ============================================================================
Write-Host "`n[2/6] Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip setuptools wheel
Write-Host "  OK: pip upgraded" -ForegroundColor Green

# ============================================================================
# STEP 3: Install Critical Packages First (avoid conflicts)
# ============================================================================
Write-Host "`n[3/6] Installing critical packages (numpy, pandas)..." -ForegroundColor Yellow
Write-Host "  This prevents compilation issues..." -ForegroundColor Cyan

# Install numpy and pandas first with correct versions
pip install "numpy>=2.0.0" "pandas>=2.0.0"
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: numpy and pandas installed" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Failed to install numpy/pandas" -ForegroundColor Red
    Write-Host "  Trying alternative approach..." -ForegroundColor Yellow
    
    # Try installing latest versions explicitly
    pip install numpy pandas --upgrade
}

# ============================================================================
# STEP 4: Choose Installation Type
# ============================================================================
Write-Host "`n[4/6] Choose installation type:" -ForegroundColor Yellow
Write-Host "  1) MINIMAL - Quick setup, Phase 1 only (~30 packages, 3 minutes)" -ForegroundColor Cyan
Write-Host "  2) FULL - All features, Phases 1-4 (~150 packages, 20 minutes)" -ForegroundColor Cyan
Write-Host ""
$Choice = Read-Host "Enter choice (1 or 2)"

if ($Choice -eq "1") {
    Write-Host "`n  Installing MINIMAL requirements..." -ForegroundColor Yellow
    $RequirementsFile = "requirements-minimal.txt"
} else {
    Write-Host "`n  Installing FULL requirements..." -ForegroundColor Yellow
    $RequirementsFile = "requirements.txt"
}

# ============================================================================
# STEP 5: Install Requirements
# ============================================================================
Write-Host "`n[5/6] Installing from $RequirementsFile..." -ForegroundColor Yellow

pip install -r $RequirementsFile
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: Requirements installed" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Some packages may have failed" -ForegroundColor Yellow
    Write-Host "  Continuing anyway..." -ForegroundColor Yellow
}

# ============================================================================
# STEP 6: Handle TA-Lib (Optional)
# ============================================================================
Write-Host "`n[6/6] Checking TA-Lib installation..." -ForegroundColor Yellow

$TalibCheck = python -c "import talib; print('OK')" 2>&1
if ($TalibCheck -match "OK") {
    Write-Host "  OK: TA-Lib already installed" -ForegroundColor Green
} else {
    Write-Host "  TA-Lib not installed (this is OK)" -ForegroundColor Yellow
    Write-Host "  You can install it later from:" -ForegroundColor Cyan
    Write-Host "  https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib" -ForegroundColor Cyan
    Write-Host "  Using pandas-ta as alternative (already installed)" -ForegroundColor Green
}

# ============================================================================
# VERIFY INSTALLATION
# ============================================================================
Write-Host "`n" + ("=" * 80) -ForegroundColor Cyan
Write-Host "INSTALLATION COMPLETE!" -ForegroundColor Green
Write-Host ("=" * 80) -ForegroundColor Cyan

Write-Host "`nVerifying critical packages..." -ForegroundColor Yellow

$TestScript = @"
import sys
errors = []
try:
    import pandas
    import numpy
    print(f'  ✓ pandas {pandas.__version__}')
    print(f'  ✓ numpy {numpy.__version__}')
except ImportError as e:
    errors.append(str(e))
    print(f'  ✗ pandas/numpy: {e}')

try:
    import pydantic
    import structlog
    print(f'  ✓ pydantic {pydantic.__version__}')
    print('  ✓ structlog')
except ImportError as e:
    errors.append(str(e))
    print(f'  ✗ config packages: {e}')

try:
    from alpaca.trading.client import TradingClient
    print('  ✓ alpaca-py')
except ImportError as e:
    errors.append(str(e))
    print(f'  ✗ alpaca-py: {e}')

if errors:
    print('\nSome packages missing - try: pip install -r $RequirementsFile')
    sys.exit(1)
else:
    print('\nAll critical packages installed successfully!')
    sys.exit(0)
"@

$TestScript | python
$VerifyResult = $LASTEXITCODE

Write-Host ""

if ($VerifyResult -eq 0) {
    Write-Host "✅ VERIFICATION PASSED" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Configure API keys:" -ForegroundColor White
    Write-Host "     notepad config\.env.local" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  2. Run tests:" -ForegroundColor White
    Write-Host "     python test_section5_final.py" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  3. Start paper trading:" -ForegroundColor White
    Write-Host "     python entry_paper.py" -ForegroundColor Cyan
} else {
    Write-Host "⚠️  VERIFICATION FAILED" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Try reinstalling:" -ForegroundColor Yellow
    Write-Host "  pip install -r $RequirementsFile --no-cache-dir" -ForegroundColor Cyan
}

Write-Host ""
