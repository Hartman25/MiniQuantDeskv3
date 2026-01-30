# 📦 REQUIREMENTS GUIDE - MiniQuantDesk v2

## Overview

This project now has **THREE** requirements files for different installation scenarios:

### 1. **requirements-minimal.txt** (Quick Start - 30 packages)
**Use for:** Fast setup, Phase 1 validation, minimal paper trading
**Install time:** ~2-3 minutes
**Size:** ~500 MB

```powershell
pip install -r requirements-minimal.txt
```

**Includes:**
- ✅ Core config & logging (pydantic, structlog, dotenv)
- ✅ Alpaca broker integration
- ✅ Basic data processing (pandas, numpy)
- ✅ SQLite persistence
- ✅ WebSocket streaming
- ✅ Essential utilities (pytz, requests, tenacity)

**Excludes:**
- ❌ Machine Learning (TensorFlow, PyTorch, scikit-learn)
- ❌ Advanced visualization (plotly, dash)
- ❌ Development tools (pytest, black, mypy)
- ❌ Cloud integrations (AWS, Azure, GCP)


### 2. **requirements.txt** (Full System - 150+ packages)
**Use for:** Complete installation with all Phase 1-4 capabilities
**Install time:** ~15-30 minutes
**Size:** ~5-8 GB (with ML frameworks)

```powershell
pip install -r requirements.txt
```

**Includes EVERYTHING:**
- ✅ All minimal requirements
- ✅ Machine Learning (TensorFlow, PyTorch, Keras)
- ✅ Advanced ML (XGBoost, LightGBM, CatBoost, Optuna)
- ✅ Statistical analysis (scipy, statsmodels, prophet)
- ✅ Visualization (plotly, dash, matplotlib, seaborn)
- ✅ Backtesting frameworks (backtrader, vectorbt)
- ✅ Cloud storage (AWS S3, Google Cloud, Azure)
- ✅ Notifications (Discord, Twilio, Slack)
- ✅ Monitoring (Prometheus, Sentry, OpenTelemetry)
- ✅ Web frameworks (FastAPI, Flask, Uvicorn)
- ✅ Performance tools (numba, cython, profilers)
- ✅ Security (cryptography, JWT, bcrypt)
- ✅ Everything else you might possibly need

**Phase mapping:**
- Phase 1: Core trading + data + persistence
- Phase 2: Async + monitoring + notifications  
- Phase 3: Machine learning + shadow mode
- Phase 4: Advanced ML + optimization + cloud


### 3. **requirements-dev.txt** (Development Tools - 30 packages)
**Use for:** Development, testing, documentation
**Install time:** ~3-5 minutes
**Size:** ~800 MB

```powershell
# Install on TOP of requirements.txt or requirements-minimal.txt
pip install -r requirements.txt -r requirements-dev.txt
```

**Includes:**
- ✅ Testing (pytest, coverage, mocking, hypothesis)
- ✅ Code quality (black, flake8, mypy, pylint, bandit)
- ✅ Profiling (line_profiler, memory_profiler, py-spy)
- ✅ Documentation (sphinx, mkdocs, pdoc)
- ✅ Interactive (Jupyter, IPython, ipywidgets)
- ✅ Debugging (ipdb, rich, watchdog)

---

## 🚀 RECOMMENDED INSTALLATION PATHS

### Path A: Quick Start (Recommended for First Time)
**Goal:** Get paper trading running ASAP

```powershell
# Step 1: Install minimal requirements
pip install -r requirements-minimal.txt

# Step 2: Install TA-Lib from wheel (Windows)
# Download from: https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
# pip install TA_Lib-0.4.28-cp313-cp313-win_amd64.whl

# Step 3: Setup environment
Copy-Item config\.env.local.template config\.env.local
notepad config\.env.local

# Step 4: Test
python test_section5_final.py
python entry_paper.py
```

**Time:** 10 minutes  
**Result:** Working paper trading system


### Path B: Full Installation (Recommended for Serious Development)
**Goal:** Install everything for all phases

```powershell
# Step 1: Upgrade pip
python -m pip install --upgrade pip setuptools wheel

# Step 2: Install full requirements (takes 15-30 minutes)
pip install -r requirements.txt

# Step 3: Add development tools (optional)
pip install -r requirements-dev.txt

# Step 4: Handle TA-Lib if it failed (common on Windows)
# Download wheel and install manually
# pip install TA_Lib-0.4.28-cp313-cp313-win_amd64.whl

# Step 5: Setup environment
Copy-Item config\.env.local.template config\.env.local
notepad config\.env.local

# Step 6: Verify
python test_section5_final.py
```

**Time:** 30-45 minutes  
**Result:** Complete system with all Phase 1-4 capabilities


### Path C: Production Minimal (For Deployment)
**Goal:** Smallest footprint for production trading

```powershell
# Use minimal + only what you actually need
pip install -r requirements-minimal.txt
pip install discord-webhook prometheus-client  # Add specific extras
```

**Time:** 5 minutes  
**Result:** Lean production deployment

---

## 🔧 TROUBLESHOOTING

### TA-Lib Installation Fails (Very Common on Windows)

**Symptom:**
```
error: Microsoft Visual C++ 14.0 or greater is required
```

**Solution:**
```powershell
# Download pre-built wheel from:
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib

# For Python 3.13 on Windows 64-bit:
# Download: TA_Lib-0.4.28-cp313-cp313-win_amd64.whl

# Install:
pip install TA_Lib-0.4.28-cp313-cp313-win_amd64.whl

# Or skip TA-Lib entirely (use pandas-ta instead):
# Comment out TA-Lib>=0.4.28 in requirements file
```


### TensorFlow/PyTorch Installation Issues

**Problem:** Large packages, slow download, or GPU issues

**Solution:**
```powershell
# CPU-only versions (much smaller, faster install):
pip install tensorflow-cpu>=2.15.0
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Or skip ML packages until Phase 3:
# pip install -r requirements-minimal.txt
```


### Out of Memory During Installation

**Problem:** pip runs out of memory installing large packages

**Solution:**
```powershell
# Install packages one at a time (slow but reliable):
pip install --no-cache-dir -r requirements.txt

# Or install in batches:
pip install pydantic python-dotenv pyyaml structlog
pip install alpaca-py pandas numpy sqlalchemy
pip install plotly dash matplotlib
# ... etc
```


### Dependency Conflicts

**Problem:** Package X requires Y<2.0 but Z requires Y>=2.0

**Solution:**
```powershell
# Use minimal requirements first
pip install -r requirements-minimal.txt

# Then add packages incrementally
pip install plotly dash
pip install scikit-learn xgboost

# Check for conflicts:
pip check
```


### Slow Installation

**Problem:** Installation takes forever

**Tips:**
```powershell
# Use faster mirror (if on slow connection):
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Install from cached wheels (after first install):
pip install -r requirements.txt --no-index --find-links=./wheelhouse

# Skip large ML packages until needed:
pip install -r requirements-minimal.txt
```

---

## 📊 PACKAGE COUNT SUMMARY

| File | Packages | Install Time | Disk Space | Use Case |
|------|----------|--------------|------------|----------|
| **requirements-minimal.txt** | ~30 | 2-3 min | ~500 MB | Quick start, Phase 1 |
| **requirements.txt** | ~150 | 15-30 min | 5-8 GB | Full system, all phases |
| **requirements-dev.txt** | ~30 | 3-5 min | ~800 MB | Development tools |
| **Total (Full + Dev)** | ~180 | 20-35 min | 6-9 GB | Complete development env |

---

## 🎯 WHAT TO INSTALL RIGHT NOW

### If You're Just Getting Started:
```powershell
pip install -r requirements-minimal.txt
```

### If You Want Everything:
```powershell
pip install -r requirements.txt
```

### If You're Developing:
```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

---

## 📋 WHAT'S ACTUALLY NEEDED BY PHASE

### Phase 1 (Current - Paper Trading)
**REQUIRED:**
- pydantic, dotenv, yaml (config)
- structlog, json-logger (logging)
- alpaca-py (broker)
- pandas, numpy (data)
- sqlalchemy (persistence)
- websockets (streaming)
- pytz, dateutil (time)

**OPTIONAL:**
- polygon (better market data)
- discord-webhook (notifications)
- pytest (testing)


### Phase 2 (Scanners + Reports)
**ADDS:**
- aiohttp, asyncio (async operations)
- plotly, dash (visualization)
- reportlab (PDF reports)
- APScheduler (scheduling)


### Phase 3 (Shadow Mode + Strategy Selection)
**ADDS:**
- scikit-learn (ML models)
- optuna (hyperparameter tuning)
- scipy, statsmodels (statistics)
- mlflow (experiment tracking)


### Phase 4 (Advanced Optimization)
**ADDS:**
- tensorflow, torch (deep learning)
- xgboost, lightgbm, catboost (advanced ML)
- ray[tune] (distributed training)
- boto3 (cloud storage)
- prometheus, sentry (monitoring)

---

## 💡 PRO TIPS

### Speed Up Future Installs
```powershell
# Create wheel cache:
pip wheel -r requirements.txt -w wheelhouse

# Future installs use cached wheels:
pip install -r requirements.txt --no-index --find-links=wheelhouse
```

### Check What's Installed
```powershell
# List all packages:
pip list

# Check for conflicts:
pip check

# Show package info:
pip show alpaca-py

# List outdated:
pip list --outdated
```

### Create Custom Requirements
```powershell
# Export current environment:
pip freeze > requirements-frozen.txt

# Install exact versions:
pip install -r requirements-frozen.txt
```

### Virtual Environment Best Practices
```powershell
# Always use virtual environments:
python -m venv venv
venv\Scripts\Activate.ps1

# Separate environments for different purposes:
python -m venv venv-minimal    # Minimal for quick tests
python -m venv venv-full       # Full for development
python -m venv venv-prod       # Production minimal
```

---

## 🚨 CRITICAL NOTES

### Windows-Specific Issues
1. **TA-Lib** - Almost always requires wheel installation
2. **uvloop** - Not available (Unix only, already handled in requirements.txt)
3. **Long paths** - May need to enable long path support in Windows

### GPU Support (Optional)
If you want GPU acceleration for ML:
```powershell
# Install CUDA toolkit first (NVIDIA GPUs only)
# Then install GPU versions:
pip install tensorflow[and-cuda]
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### Production Deployment
- Remove dev tools (testing, profiling, jupyter)
- Use `requirements-minimal.txt` + specific additions
- Pin exact versions (use `pip freeze`)
- Consider Docker for reproducibility

### Security
```powershell
# Check for security issues:
pip install safety
safety check --file requirements.txt

# Or use:
pip install bandit
bandit -r core/
```

---

## ✅ VERIFICATION

After installation, verify everything works:

```powershell
# Test imports:
python -c "import pandas, numpy, pydantic, alpaca, structlog; print('Core OK')"
python -c "from core.di.container import Container; print('Container OK')"

# Run integration test:
python test_section5_final.py

# Check installed packages:
pip list | grep -E "alpaca|pandas|pydantic|structlog"
```

---

## 📚 ADDITIONAL RESOURCES

- **TA-Lib Wheels:** https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib
- **PyTorch Installation:** https://pytorch.org/get-started/locally/
- **TensorFlow GPU:** https://www.tensorflow.org/install/pip
- **Alpaca Docs:** https://docs.alpaca.markets/
- **Polygon Docs:** https://polygon.io/docs/

---

## 🎯 QUICK DECISION TREE

```
Do you need to test RIGHT NOW?
├─ YES → pip install -r requirements-minimal.txt
└─ NO
    │
    Do you plan to use ML features (Phase 3/4)?
    ├─ YES → pip install -r requirements.txt
    └─ NO → pip install -r requirements-minimal.txt
        │
        Will you be developing/testing code?
        ├─ YES → pip install -r requirements-dev.txt (also)
        └─ NO → You're done!
```

---

**Last Updated:** January 25, 2026  
**Python Version:** 3.10+ (tested with 3.13)  
**Platform:** Windows 10/11 (adaptable for macOS/Linux)

---

For questions or issues, check:
1. This guide
2. POWERSHELL_COMMANDS.md
3. setup_environment.ps1
