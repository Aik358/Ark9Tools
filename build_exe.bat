@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=C:\Python314\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [安装] PyInstaller...
    "%PYTHON%" -m pip install pyinstaller --timeout 60
    if errorlevel 1 goto :fail
)

echo [1/2] 构建 Ark9Tools 程序文件...
"%PYTHON%" -m PyInstaller --noconfirm --clean MAA_PixelPainter.spec
if errorlevel 1 goto :fail

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do set "ISCC=%%I"
)
if not exist "%ISCC%" (
    echo.
    echo [缺少工具] 未安装 Inno Setup 6。
    echo 请执行：winget install --id JRSoftware.InnoSetup -e
    echo 安装后重新运行 build_exe.bat。
    goto :fail
)

echo [2/2] 生成 Windows 安装程序...
"%ISCC%" Ark9Tools.iss
if errorlevel 1 goto :fail

echo.
echo [完成] 安装程序：dist\Ark9Tools_Setup.exe
echo 用户可选择安装位置，安装后从开始菜单或桌面启动。
pause
exit /b 0

:fail
echo.
echo [失败] 安装程序构建未完成。
pause
exit /b 1
