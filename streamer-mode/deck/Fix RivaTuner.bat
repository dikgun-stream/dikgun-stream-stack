@echo off
rem Stream Deck launcher. Runs hidden so no console flashes.
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0restart-rivatuner.ps1 -Fix"

