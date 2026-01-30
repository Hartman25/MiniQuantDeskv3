# ============================================================================
# MiniQuantDesk v2 - Quick PowerShell Setup Commands
# ============================================================================
# Copy-paste these commands into PowerShell
# ============================================================================

# OPTION 1: AUTOMATED SETUP (Recommended)
# ============================================================================
# Run the automated setup script

cd C:\Users\Zacha\Desktop\MiniQuantDeskv2
.\setup_environment.ps1


# OPTION 2: MANUAL SETUP (Step-by-step)
# ============================================================================

# Step 1: Navigate to project
cd C:\Users\Zacha\Desktop\MiniQuantDeskv2

# Step 2: Verify Python (need 3.10+)
python --version

# Step 3: Create virtual environment (if not exists)
python -m venv venv

# Step 4: Activate virtual environment
# NOTE: If you get execution policy error, run as Administrator:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
venv\Scripts\Activate.ps1

# Step 5: Upgrade pip
python -m pip install --upgrade pip

# Step 6: Install dependencies
pip install -r requirements.txt

# Step 7: Copy environment template
Copy-Item config\.env.local.template config\.env.local

# Step 8: Edit credentials (open in notepad)
notepad config\.env.local


# ============================================================================
# DAILY USAGE COMMANDS
# ============================================================================

# Activate environment (do this every time you open PowerShell)
cd C:\Users\Zacha\Desktop\MiniQuantDeskv2
venv\Scripts\Activate.ps1

# Deactivate environment
deactivate

# Run paper trading
python entry_paper.py

# Run integration test
python test_integration_complete.py

# Run protection migration verification
python test_section5_final.py


# ============================================================================
# TROUBLESHOOTING
# ============================================================================

# If "script not digitally signed" error:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# If pip install fails:
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt --no-cache-dir

# If TA-Lib fails to install:
# Download wheel from: https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
# Install with: pip install TA_Lib‑0.4.28‑cp313‑cp313‑win_amd64.whl

# Check what's installed:
pip list

# Check Python path:
where python


# ============================================================================
# ENVIRONMENT VARIABLES CHECK
# ============================================================================

# View current environment variables
Get-Content config\.env.local

# Test if variables are loaded
python -c "from dotenv import load_dotenv; import os; load_dotenv('config/.env.local'); print('PAPER_TRADING:', os.getenv('PAPER_TRADING'))"


# ============================================================================
# GIT COMMANDS (if needed)
# ============================================================================

# Check status
git status

# Stage all changes
git add .

# Commit with message
git commit -m "feat: Complete protection system migration"

# Push to remote
git push origin main


# ============================================================================
# CONFIGURATION FILES
# ============================================================================

# Edit main config
notepad config\config.yaml

# Edit micro account config
notepad config\config_micro.yaml

# Edit environment secrets
notepad config\.env.local


# ============================================================================
# LOG VIEWING
# ============================================================================

# View latest system log
Get-Content logs\system\*.log -Tail 50

# View latest trading log
Get-Content logs\trading\*.log -Tail 50

# View today's logs with timestamps
Get-ChildItem logs\*\*.log | Where-Object {$_.LastWriteTime -gt (Get-Date).Date}


# ============================================================================
# QUICK TEST COMMANDS
# ============================================================================

# Test Container initialization
python -c "from core.di.container import Container; c = Container(); c.initialize('config/config.yaml'); print('Container OK')"

# Test ProtectionManager
python -c "from core.di.container import Container; c = Container(); c.initialize('config/config.yaml'); p = c.get_protections(); print('Protections:', len(p.get_all_statuses()))"

# Test broker connection (paper)
python -c "from core.brokers.alpaca import AlpacaBrokerConnector; from core.config.loader import ConfigLoader; cfg = ConfigLoader('config/config.yaml').load(); broker = AlpacaBrokerConnector(cfg.broker, paper=True); print('Paper account:', broker.get_account())"


# ============================================================================
# USEFUL ALIASES (add to PowerShell profile)
# ============================================================================

# To add these permanently, edit your PowerShell profile:
# notepad $PROFILE

# Add these lines to your profile:
# function mqd { Set-Location C:\Users\Zacha\Desktop\MiniQuantDeskv2 }
# function mqd-activate { Set-Location C:\Users\Zacha\Desktop\MiniQuantDeskv2; .\venv\Scripts\Activate.ps1 }
# function mqd-paper { Set-Location C:\Users\Zacha\Desktop\MiniQuantDeskv2; python entry_paper.py }
# function mqd-test { Set-Location C:\Users\Zacha\Desktop\MiniQuantDeskv2; python test_integration_complete.py }

# Then use:
# mqd              - Navigate to project
# mqd-activate     - Navigate and activate environment
# mqd-paper        - Run paper trading
# mqd-test         - Run integration test
