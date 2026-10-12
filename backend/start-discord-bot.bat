@echo off
REM Double-click to run the SachScan Discord bot. Keep this window open.
cd /d "%~dp0.."
echo.
echo  SachScan Discord bot starting...  (Ctrl+C to stop)
echo.
"backend\.venv\Scripts\python.exe" -m bots.discord_bot
pause
