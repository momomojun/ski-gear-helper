@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SkiDeals
if not exist ".venv\Scripts\python.exe" (
  echo [SkiDeals] First run: creating Python environment and installing packages, about 1 minute...
  python -m venv .venv || (echo Python 3.11+ not found. Please install it from python.org & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo Package install failed & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m skideals serve --open
pause
