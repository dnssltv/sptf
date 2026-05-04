@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH.
  echo Install Python 3.10+ and check "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo.
echo Starting UI...
echo.
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "spotify_import_gui.py"
) else (
  ".venv\Scripts\python.exe" spotify_import_gui.py
)

echo.
exit /b 0
