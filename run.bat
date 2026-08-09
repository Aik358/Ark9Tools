@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

REM Relaunch this script elevated when needed for game window control.
fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator permission...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:ComSpec -ArgumentList '/c ""%~f0""' -Verb RunAs" >nul 2>&1
    exit /b
)

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Recommended HDR capture environment was not found.
    echo Run setup_hdr_env.bat once to create the Python 3.13 environment.
    pause
    exit /b 1
)

echo Checking HDR capture dependencies...
"%PYTHON%" -c "import hdrcapture, PySide6, numpy, PIL, win32gui, mss, cv2" 2>nul
if errorlevel 1 (
    echo Dependency installation is incomplete. Run setup_hdr_env.bat.
    pause
    exit /b 1
)

echo.
echo Starting Ark9Tools with hdrcapture auto SDR mode...
"%PYTHON%" main.py
pause
endlocal
