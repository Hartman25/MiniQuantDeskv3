# ============================================================================
# MiniQuantDesk v2 - PowerShell Environment Setup
# ============================================================================
# Run this script in PowerShell to set up your development environment
# Compatible with: Windows 10/11, PowerShell 5.1+
# ============================================================================

Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "MiniQuantDesk v2 - Environment Setup" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan

# Navigate to project directory
$ProjectRoot = "C:\Users\Zacha\Desktop\MiniQuantDeskv2"
Set-Location $ProjectRoot
Write-Host "`n[1/8] Set working directory: $ProjectRoot" -ForegroundColor Green

# ============================================================================
# STEP 1: Verify Python Installation
# ============================================================================
Write-Host "`n[2/8] Verifying Python installation..." -ForegroundColor Yellow

try {
    $PythonVersion = python --version 2>&1
    Write-Host "  Found: $PythonVersion" -ForegroundColor Green
    
    # Check Python version (need 3.10+)
    $VersionMatch = [regex]::Match($PythonVersion, "Python (\d+)\.(\d+)")
    if ($VersionMatch.Success) {
        $Major = [int]$VersionMatch.Groups[1].Value
        $Minor = [int]$VersionMatch.Groups[2].Value
        
        if ($Major -ge 3 -and $Minor -ge 10) {
            Write-Host "  OK: Python $Major.$Minor meets requirement (3.10+)" -ForegroundColor Green
        } else {
            Write-Host "  ERROR: Python $Major.$Minor is too old. Need 3.10+" -ForegroundColor Red
            Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
            exit 1
        }
    }
} catch {
    Write-Host "  ERROR: Python not found in PATH" -ForegroundColor Red
    Write-Host "  Install from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Yellow
    exit 1
}

# ============================================================================
# STEP 2: Create Virtual Environment (if not exists)
# ============================================================================
Write-Host "`n[3/8] Setting up virtual environment..." -ForegroundColor Yellow

$VenvPath = "$ProjectRoot\venv"
if (Test-Path $VenvPath) {
    Write-Host "  Virtual environment already exists: $VenvPath" -ForegroundColor Green
} else {
    Write-Host "  Creating virtual environment: $VenvPath" -ForegroundColor Yellow
    python -m venv venv
    Write-Host "  OK: Virtual environment created" -ForegroundColor Green
}

# ============================================================================
# STEP 3: Activate Virtual Environment
# ============================================================================
Write-Host "`n[4/8] Activating virtual environment..." -ForegroundColor Yellow

$ActivateScript = "$VenvPath\Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    # Check execution policy
    $ExecutionPolicy = Get-ExecutionPolicy
    if ($ExecutionPolicy -eq "Restricted") {
        Write-Host "  WARNING: Execution policy is Restricted" -ForegroundColor Yellow
        Write-Host "  Run this command as Administrator to enable scripts:" -ForegroundColor Yellow
        Write-Host "  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Cyan
        Write-Host "`n  Or activate manually with:" -ForegroundColor Yellow
        Write-Host "  venv\Scripts\Activate.ps1" -ForegroundColor Cyan
    } else {
        & $ActivateScript
        Write-Host "  OK: Virtual environment activated" -ForegroundColor Green
    }
} else {
    Write-Host "  ERROR: Activation script not found" -ForegroundColor Red
    exit 1
}

# ============================================================================
# STEP 4: Upgrade pip, setuptools, wheel
# ============================================================================
Write-Host "`n[5/8] Upgrading pip, setuptools, wheel..." -ForegroundColor Yellow

python -m pip install --upgrade pip setuptools wheel
Write-Host "  OK: Package managers updated" -ForegroundColor Green

# ============================================================================
# STEP 5: Install Requirements
# ============================================================================
Write-Host "`n[6/8] Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "  This may take 5-10 minutes on first install..." -ForegroundColor Cyan

pip install -r requirements.txt --break-system-packages
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: All dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Dependency installation failed" -ForegroundColor Red
    Write-Host "  Try running: pip install -r requirements.txt" -ForegroundColor Yellow
}

# ============================================================================
# STEP 6: Setup Environment File
# ============================================================================
Write-Host "`n[7/8] Setting up environment configuration..." -ForegroundColor Yellow

$EnvTemplate = "$ProjectRoot\config\.env.local.template"
$EnvLocal = "$ProjectRoot\config\.env.local"

if (Test-Path $EnvLocal) {
    Write-Host "  .env.local already exists" -ForegroundColor Green
    Write-Host "  Location: $EnvLocal" -ForegroundColor Cyan
} else {
    Write-Host "  Creating .env.local from template..." -ForegroundColor Yellow
    Copy-Item $EnvTemplate $EnvLocal
    Write-Host "  OK: .env.local created" -ForegroundColor Green
    Write-Host "  IMPORTANT: Edit this file with your credentials:" -ForegroundColor Yellow
    Write-Host "  $EnvLocal" -ForegroundColor Cyan
}

# ============================================================================
# STEP 7: Verify Installation
# ============================================================================
Write-Host "`n[8/8] Verifying installation..." -ForegroundColor Yellow

# Check critical imports
$TestScript = @"
import sys
try:
    import pandas
    import numpy
    import pydantic
    import alpaca
    import structlog
    print('OK: All critical packages importable')
    sys.exit(0)
except ImportError as e:
    print(f'ERROR: Missing package - {e}')
    sys.exit(1)
"@

$TestScript | python
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: Installation verified" -ForegroundColor Green
} else {
    Write-Host "  WARNING: Some packages may not be installed correctly" -ForegroundColor Yellow
}

# ============================================================================
# SETUP COMPLETE
# ============================================================================
Write-Host "`n" + ("=" * 80) -ForegroundColor Cyan
Write-Host "SETUP COMPLETE!" -ForegroundColor Green
Write-Host ("=" * 80) -ForegroundColor Cyan

Write-Host "`nNext Steps:" -ForegroundColor Yellow
Write-Host "  1. Edit your API credentials:" -ForegroundColor White
Write-Host "     notepad $EnvLocal" -ForegroundColor Cyan
Write-Host ""
Write-Host "  2. Configure your Alpaca API keys from:" -ForegroundColor White
Write-Host "     https://app.alpaca.markets/paper/dashboard/overview" -ForegroundColor Cyan
Write-Host ""
Write-Host "  3. Verify PAPER_TRADING=true in .env.local" -ForegroundColor White
Write-Host ""
Write-Host "  4. Run integration test:" -ForegroundColor White
Write-Host "     python test_integration_complete.py" -ForegroundColor Cyan
Write-Host ""
Write-Host "  5. Start paper trading:" -ForegroundColor White
Write-Host "     python entry_paper.py" -ForegroundColor Cyan
Write-Host ""

Write-Host "Virtual Environment:" -ForegroundColor Yellow
Write-Host "  Activate:   venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "  Deactivate: deactivate" -ForegroundColor Cyan
Write-Host ""

Write-Host "Project Root: $ProjectRoot" -ForegroundColor Yellow
Write-Host ""
