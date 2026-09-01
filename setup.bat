@echo off
REM ============================================================
REM  Gear360 Hub - Windows setup
REM  Creates a virtual environment and installs dependencies.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === Gear360 Hub setup ===
echo.

if not exist .venv (
  echo Creating virtual environment...
  py -3 -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat

python -m pip install --upgrade pip
pip install -r requirements.txt || goto :error

echo.
echo === Optional: AI extras (face recognition) ===
set /p INSTALL_AI="Install face recognition extras now? [y/N] "
if /i "%INSTALL_AI%"=="y" (
  pip install -r requirements-ai.txt
)

echo.
echo === FFmpeg check ===
where ffmpeg.exe >nul 2>nul
if %errorlevel%==0 (
  echo ffmpeg found in PATH.
) else (
  if exist runtime\ffmpeg\ffmpeg.exe (
    echo ffmpeg found in runtime\ffmpeg\
  ) else (
    echo WARNING: ffmpeg.exe was not found.
    echo Download a Windows build from https://www.gyan.dev/ffmpeg/builds/
    echo and place ffmpeg.exe + ffprobe.exe inside runtime\ffmpeg\
    echo Recording will stay disabled until FFmpeg is available.
  )
)

echo.
echo Setup complete. Start the Hub with:  run_gateway.bat
pause
exit /b 0

:error
echo Setup failed.
pause
exit /b 1
