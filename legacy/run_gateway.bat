@echo off
REM ============================================================
REM  Gear360 Hub - start (production-ish, localhost by default)
REM  Binds to 127.0.0.1 unless you enable LAN access in Settings,
REM  then run with:  set HUB_HOST=0.0.0.0 && run_gateway.bat
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
echo Starting Gear360 Hub on http://%HUB_HOST%:%HUB_PORT%
python -m uvicorn app.main:app --host %HUB_HOST% --port %HUB_PORT%
