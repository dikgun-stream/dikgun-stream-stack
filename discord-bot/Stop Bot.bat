@echo off
rem Launches hidden so it can be bound to a Stream Deck / Stream Dock button
rem without a console window flashing on screen.
start "" /min powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0bot-control.ps1" stop
