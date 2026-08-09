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

where python >nul 2>&1
if errorlevel 1 (
    echo Python 3.10+ was not found.
    pause
    exit /b 1
)

echo Checking dependencies...
python -c "import PySide6, numpy, PIL, win32gui, mss" 2>nul
if errorlevel 1 (
    echo Installing required dependencies...
    python -m pip install pywin32 PySide6 numpy Pillow mss -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

python -c "import cv2" 2>nul
if errorlevel 1 (
    python -m pip install opencv-python -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 2>nul
)

echo.
echo Starting Ark9Tools...
python main.py
pause
endlocal
