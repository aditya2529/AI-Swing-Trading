@echo off
REM ============================================================
REM  Swing paper-trading — daily evening run (LOCAL).
REM  1) fetch today's prices  2) monthly bot  3) weekly bot
REM  Each bot updates its own ledger and pings Telegram.
REM  Scheduled by Windows Task Scheduler on weekday evenings.
REM ============================================================
cd /d "%~dp0"
set PAPER_MODE=1

echo [1/3] Fetching today's prices...
python -m live.data_feed

echo [2/3] Monthly bot...
python -m live.replay_runner monthly

echo [3/3] Weekly bot...
python -m live.replay_runner weekly

echo.
echo Daily run complete.
