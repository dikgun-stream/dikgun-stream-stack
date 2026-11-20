@echo off
rem Stream Deck launcher. Runs hidden so no console flashes.
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0rivatuner-profile.ps1 -Name "Gamma 1""

