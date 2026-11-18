@echo off
REM Enter streamer mode: clean desktop + all streaming apps tiled on the Hisense.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Streamer-Mode.ps1" -Mode stream
if errorlevel 1 pause
