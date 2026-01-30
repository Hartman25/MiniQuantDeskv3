# System Watchdog Script
# Monitors MiniQuantDesk process and restarts if crashed

# ============================================================================
# CONFIGURATION
# ============================================================================

$CONFIG = @{
    ProcessName = "python"
    ScriptPath = "C:\Users\Zacha\Desktop\MiniQuantDeskv2\scripts\run_paper_trading.py"
    CheckInterval = 300  # Check every 5 minutes
    MaxRestarts = 10     # Max restarts per hour
    LogFile = "C:\Users\Zacha\Desktop\MiniQuantDeskv2\logs\watchdog\watchdog.log"
}

# ============================================================================
# LOGGING
# ============================================================================

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    
    # Console output
    Write-Host $logMessage
    
    # File output
    $logDir = Split-Path $CONFIG.LogFile
    if (!(Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -Path $CONFIG.LogFile -Value $logMessage
}

# ============================================================================
# PROCESS DETECTION
# ============================================================================

function Test-TradingProcessRunning {
    $processes = Get-Process -Name $CONFIG.ProcessName -ErrorAction SilentlyContinue
    
    foreach ($proc in $processes) {
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)").CommandLine
        if ($commandLine -like "*$($CONFIG.ScriptPath)*") {
            return $true
        }
    }
    
    return $false
}

# ============================================================================
# RESTART LOGIC
# ============================================================================

$restartHistory = @()

function Test-RestartAllowed {
    $oneHourAgo = (Get-Date).AddHours(-1)
    $recentRestarts = $restartHistory | Where-Object { $_ -gt $oneHourAgo }
    
    return $recentRestarts.Count -lt $CONFIG.MaxRestarts
}

function Start-TradingProcess {
    Write-Log "Starting trading process..."
    
    try {
        $pythonPath = "C:\Users\Zacha\Desktop\MiniQuantDesk\.venv\Scripts\python.exe"
        
        # Start process in background
        Start-Process -FilePath $pythonPath `
                      -ArgumentList $CONFIG.ScriptPath `
                      -WorkingDirectory "C:\Users\Zacha\Desktop\MiniQuantDeskv2" `
                      -WindowStyle Hidden
        
        # Record restart time
        $restartHistory += Get-Date
        
        Write-Log "Process started successfully"
        return $true
    }
    catch {
        Write-Log "ERROR: Failed to start process: $_"
        return $false
    }
}

# ============================================================================
# MAIN WATCHDOG LOOP
# ============================================================================

Write-Log "=== Watchdog started ==="
Write-Log "Process name: $($CONFIG.ProcessName)"
Write-Log "Script path: $($CONFIG.ScriptPath)"
Write-Log "Check interval: $($CONFIG.CheckInterval) seconds"

while ($true) {
    try {
        if (Test-TradingProcessRunning) {
            Write-Log "Process is running - OK"
        }
        else {
            Write-Log "WARNING: Process not running"
            
            if (Test-RestartAllowed) {
                Write-Log "Attempting automatic restart..."
                
                if (Start-TradingProcess) {
                    Write-Log "Restart successful"
                    
                    # Wait 30 seconds to verify process started
                    Start-Sleep -Seconds 30
                    
                    if (Test-TradingProcessRunning) {
                        Write-Log "Process confirmed running after restart"
                    }
                    else {
                        Write-Log "ERROR: Process not running after restart attempt"
                    }
                }
                else {
                    Write-Log "ERROR: Restart failed"
                }
            }
            else {
                Write-Log "ERROR: Restart limit reached ($($CONFIG.MaxRestarts) per hour)"
                Write-Log "Manual intervention required"
                
                # Send alert (you can add Discord webhook here)
                # Invoke-RestMethod -Uri "YOUR_DISCORD_WEBHOOK" -Method Post -Body ...
            }
        }
    }
    catch {
        Write-Log "ERROR: Watchdog loop exception: $_"
    }
    
    # Wait for next check
    Start-Sleep -Seconds $CONFIG.CheckInterval
}
