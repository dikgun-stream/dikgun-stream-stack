@echo off
pwsh -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0..\Streamer-Mode.ps1" -Mode stream
