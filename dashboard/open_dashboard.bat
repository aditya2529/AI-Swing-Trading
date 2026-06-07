@echo off
REM Swing paper-trading dashboard — pull latest ledger from the VM,
REM regenerate the HTML, and open it in your default browser.
cd /d "%~dp0"
python build_dashboard.py
if exist swing_dashboard.html (
  start "" "swing_dashboard.html"
) else (
  echo Could not generate the dashboard. See messages above.
  pause
)
