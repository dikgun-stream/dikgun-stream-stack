@echo off
REM Enter dev mode: same tiling plus Streamer.bot + DeepSeek Harness, nothing hidden.
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0Streamer-Mode.ps1" -Mode dev
if errorlevel 1 pause
