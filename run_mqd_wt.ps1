# run_mqd_wt.ps1
# Requires Windows Terminal (wt.exe)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

# Commands
$scanner = "cd `"$Repo`"; python -m scanners.standalone_scanner"
$daemon  = "cd `"$Repo`"; python -m core.universe.daemon"
$bot     = "cd `"$Repo`"; `$env:UNIVERSE_MODE='hybrid'; python entry_paper.py"

# Launch Windows Terminal with 3 panes
wt -w 0 `
  new-tab --title "MQD Scanner"  powershell -NoExit -Command $scanner `
  ; split-pane -H --title "Universe Daemon" powershell -NoExit -Command $daemon `
  ; split-pane -V --title "Trading Bot"     powershell -NoExit -Command $bot
