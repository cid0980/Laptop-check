@echo off
setlocal enabledelayedexpansion
title Laptop Check Pro - one-click Windows build
color 0B
cd /d "%~dp0.."

echo ============================================================
echo   Laptop Check Pro - building the single-file .exe
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  [X] Python was not found on this PC.
    echo      Install Python 3.11 or 3.12 from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" during the install.
    echo.
    pause
    exit /b 1
)

python -c "import sys; print('  Python', sys.version.split()[0])"
python -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3,11),(3,12)) else 1)" 2>nul
if errorlevel 1 (
    echo  [!] Note: Python 3.11 or 3.12 is recommended. Other versions may work.
    echo.
)

echo  [1/4] Making sure PyInstaller is available...
python -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller
if errorlevel 1 (
    echo  [X] pip could not install PyInstaller. Check your internet connection.
    pause
    exit /b 1
)

echo  [2/4] Self-test (builds a demo report to prove the code runs)...
python run.py --selftest
if errorlevel 1 (
    echo  [X] The self-test FAILED. Fix the problem above before shipping an exe.
    pause
    exit /b 1
)

echo  [3/4] Packaging dist\LaptopCheckPro.exe ...
python -m PyInstaller LaptopCheckPro.spec --noconfirm --clean
if errorlevel 1 (
    echo  [X] PyInstaller failed. See the output above.
    pause
    exit /b 1
)

echo  [4/4] Checking the packaged exe...
if not exist "dist\LaptopCheckPro.exe" (
    echo  [X] dist\LaptopCheckPro.exe was not created.
    pause
    exit /b 1
)
for %%I in (dist\LaptopCheckPro.exe) do echo       size: %%~zI bytes

echo.
echo ============================================================
echo   DONE.  Ship or run:   dist\LaptopCheckPro.exe
echo   Double-click it on any Windows 10/11 PC - nothing to install.
echo   The UAC prompt is expected: the tool needs admin to read
echo   SMART data, temperatures and the panel EDID.
echo ============================================================
echo.
pause
endlocal
