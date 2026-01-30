# start_mqd.ps1 (run from anywhere)
$Repo="C:\Users\Zacha\Desktop\MiniQuantDeskv2"

wt `
  new-tab --title "Scanner" powershell -NoExit -Command "cd `"$Repo`"; python -m scanners.standalone_scanner" `
  ; split-pane -H --title "Universe Daemon" powershell -NoExit -Command "cd `"$Repo`"; python -m core.universe.daemon" `
  ; split-pane -V --title "Trading Bot" powershell -NoExit -Command "cd `"$Repo`"; `$env:UNIVERSE_MODE='hybrid'; python entry_paper.py"
