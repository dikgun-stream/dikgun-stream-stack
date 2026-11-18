@echo off
REM Start the DikGun overlay server and open the dashboard.
title DikGun Overlay Server
cd /d "%~dp0"

set PY=python
where python >nul 2>nul
if errorlevel 1 (
  set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
)

echo Starting overlay server on http://127.0.0.1:7475
start "" http://127.0.0.1:7475/
"%PY%" -m uvicorn server:app --host 127.0.0.1 --port 7475 --log-level info

pause
