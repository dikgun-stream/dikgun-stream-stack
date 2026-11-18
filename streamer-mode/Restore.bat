@echo off
REM Undo streamer mode: restore taskbar and desktop icons.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Streamer-Mode.ps1" -Restore
if errorlevel 1 pause
