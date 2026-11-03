@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ is required. Install it from python.org and enable the Python launcher.
  pause
  exit /b 1
)

py -3 -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed

py -3 -m PyInstaller --noconfirm --clean --onefile --console --name InventoryEntry windows_launcher.py
if errorlevel 1 goto failed

echo.
echo Built dist\InventoryEntry.exe
echo Keep the EXE beside app.py, requirements.txt, .streamlit, and the other app files.
pause
exit /b 0

:failed
echo.
echo Build failed. Review the error above.
pause
exit /b 1
