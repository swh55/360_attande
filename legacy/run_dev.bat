@echo off
REM ============================================================
REM  Gear360 Hub - development mode (auto reload)
REM ============================================================
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo No virtual environment found. Run setup.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
if "%HUB_HOST%"=="" set HUB_HOST=127.0.0.1
if "%HUB_PORT%"=="" set HUB_PORT=8765
python -m uvicorn app.main:app --host %HUB_HOST% --port %HUB_PORT% --reload
