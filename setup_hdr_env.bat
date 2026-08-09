@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    where uv >nul 2>&1
    if errorlevel 1 (
        echo uv was not found. Install uv first.
        pause
        exit /b 1
    )
    uv venv --python 3.13 "%~dp0.venv"
)
uv pip install --python "%~dp0.venv\Scripts\python.exe" hdrcapture==0.3.0 PySide6 numpy Pillow pywin32 mss opencv-python
if errorlevel 1 (
    echo HDR environment setup failed.
    pause
    exit /b 1
)
echo HDR SDR environment is ready.
pause
endlocal
