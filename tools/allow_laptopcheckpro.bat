@echo off
rem ---------------------------------------------------------------------------
rem  Laptop Check Pro - one-time unblock for Windows 11 Smart App Control
rem
rem  Smart App Control (SAC) hard-blocks unsigned tools like this one: there is
rem  no "Run anyway" button.  This script switches SAC to EVALUATION mode on
rem  THIS PC only, which blocks nothing but keeps SAC's logging.  A single
rem  restart is required for the change to take effect.
rem
rem  Right-click this file -> "Run as administrator".
rem ---------------------------------------------------------------------------
title Laptop Check Pro - allow unsigned tools
color 0F
echo.
echo   Laptop Check Pro - one-time Windows unblock
echo   --------------------------------------------
echo.
net session >nul 2>&1
if errorlevel 1 (
    echo   This script needs administrator rights.
    echo   Close this window, RIGHT-CLICK the file and choose
    echo   "Run as administrator", then try again.
    echo.
    pause
    exit /b 1
)

set KEY=HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy
for /f "tokens=3" %%v in ('reg query "%KEY%" /v VerifiedAndReputablePolicyState 2^>nul ^| find "VerifiedAndReputablePolicyState"') do set CUR=%%v
if not defined CUR set CUR=not present

echo   Smart App Control state on this PC right now: %CUR%
echo     (0 = off, 1 = on and blocking, 2 = evaluation - blocks nothing)
echo.
if "%CUR%"=="0x2" (
    echo   Already in evaluation mode - nothing to do.
    echo   If the exe is still blocked, restart the PC once and try again.
    echo.
    pause
    exit /b 0
)
if "%CUR%"=="0x0" (
    echo   Smart App Control is already OFF on this PC - nothing to do.
    echo   If the exe is still blocked, the block is SmartScreen, not SAC:
    echo   run the exe, click "More info", then "Run anyway" once.
    echo.
    pause
    exit /b 0
)

echo   Switching Smart App Control to EVALUATION mode (value 2)...
reg add "%KEY%" /v VerifiedAndReputablePolicyState /t REG_DWORD /d 2 /f >nul
if errorlevel 1 (
    echo   Could not write the registry value.  Are you administrator?
    pause
    exit /b 1
)
echo   Done.  Smart App Control will now observe but never block.
echo.
echo   RESTART THE PC ONCE, then double-click LaptopCheckPro.exe again.
echo.
echo   Note: evaluation mode keeps SAC's logging on.  If you would rather
echo   switch SAC fully off, run this script's command manually with /d 0.
echo.
pause
