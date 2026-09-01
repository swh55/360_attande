@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  py -3 -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :error
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
exit /b 0
:error
echo Gateway failed to start.
pause
exit /b 1
