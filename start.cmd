@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run python scripts\bootstrap.py first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" start.py
if errorlevel 1 pause
