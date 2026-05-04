@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH.
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

echo Installing build dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Failed to install runtime requirements.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check pyinstaller
if errorlevel 1 (
  echo Failed to install PyInstaller.
  pause
  exit /b 1
)

echo Building EXE...
".venv\Scripts\python.exe" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name "SpotifyImporterWizard" ^
  "spotify_import_gui.py"

if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

echo.
echo Build completed:
echo dist\SpotifyImporterWizard.exe
pause
