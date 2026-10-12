@echo off
REM One double-click: starts the backend, ngrok and the Discord bot in their own windows.
REM Anything already running is skipped. Close a window (X) to stop that part.
cd /d "%~dp0.."

netstat -ano | findstr /R /C:":3001 .*LISTENING" >nul
if errorlevel 1 (
  echo Starting backend server...
  start "SachScan server" cmd /k ""backend\.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 0.0.0.0 --port 3001 --reload --reload-dir backend"
) else (
  echo Backend server already running - skipped.
)

tasklist /FI "IMAGENAME eq ngrok.exe" | findstr /I ngrok.exe >nul
if errorlevel 1 (
  echo Starting ngrok...
  start "SachScan ngrok" cmd /k "ngrok http --domain=driveway-class-pureness.ngrok-free.dev 3001"
) else (
  echo ngrok already running - skipped.
)

wmic process where "name='python.exe'" get commandline 2>nul | findstr /C:"bots.discord_bot" >nul
if errorlevel 1 (
  echo Starting Discord bot...
  start "SachScan Discord bot" cmd /k ""backend\.venv\Scripts\python.exe" -m bots.discord_bot"
) else (
  echo Discord bot already running - skipped.
)

echo.
echo  Website:  http://localhost:3001
echo  Public:   https://driveway-class-pureness.ngrok-free.dev
echo.
echo You can close this small window; the three others keep running.
pause
