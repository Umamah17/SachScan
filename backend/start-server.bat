@echo off
REM Double-click to start the SachScan backend. Keep this window open while using the app.
cd /d "%~dp0"
echo.
echo  SachScan backend starting...
echo  Open:  http://localhost:3001/help.html
echo  (Do not open 0.0.0.0 - use localhost.)  Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 3001 --reload
echo.
echo  The server stopped. If you see an error above, send a screenshot.
pause
