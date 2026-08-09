@echo off
chcp 65001 >nul
REM ============================================================
REM  Ark9Tools exe 打包脚本（PyInstaller，带应用 Logo）
REM  产物输出到 dist\Ark9Tools.exe
REM ============================================================
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python
    pause
    exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo 安装 PyInstaller...
    python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60
)

echo 开始打包（含 Logo）...
python -m PyInstaller --noconfirm --clean ^
    --name Ark9Tools ^
    --windowed ^
    --uac-admin ^
    --icon assets\app.ico ^
    --add-data "assets;assets" ^
    main.py

if errorlevel 1 (
    echo [失败] 打包出错
    pause
    exit /b 1
)

echo.
echo [完成] 已生成: dist\MAA_PixelPainter.exe
echo 提示: 将 dist\MAA_PixelPainter 整个文件夹发给用户即可运行。
pause
