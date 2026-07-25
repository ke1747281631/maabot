@echo off
setlocal enabledelayedexpansion
title MAABot - Runtime Environment Installer

echo.
echo +============================================+
echo   MAABot - Runtime Environment Installer
echo   Components: VC++ Redist / QQ NT / NcatBot
echo +============================================+
echo.

:: -- Check admin privileges --
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Please right-click this script and select "Run as administrator"
    pause
    exit /b 1
)

:: -- Check Python --
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.9+
    echo        Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python installed: %PY_VER%
echo.

:: -- Install VC++ Runtime --
echo [2/5] Installing Visual C++ Runtime (NapCat dependency)...
set VC_URL=https://aka.ms/vs/17/release/vc_redist.x64.exe
set VC_FILE=%TEMP%\vc_redist.x64.exe

if exist "%VC_FILE%" (
    echo [SKIP] Installer already exists
) else (
    echo Downloading VC++ Runtime...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%VC_URL%' -OutFile '%VC_FILE%' -UseBasicParsing}"
    if !errorLevel! neq 0 (
        echo [ERROR] VC++ Runtime download failed, check your network
        pause
        exit /b 1
    )
)
echo Installing VC++ Runtime (silent, please wait)...
"%VC_FILE%" /install /quiet /norestart
echo [OK] VC++ Runtime installed
echo.

:: -- Install QQ NT --
echo [3/5] Installing QQ NT (NapCat compatible version)...
echo.
echo [NOTE] If QQ is already installed, please uninstall it first.
echo        Otherwise the new version may fail or be incompatible.
echo.
set /p UNINSTALL_QQ="Uninstall current QQ now? (y/n, Enter to skip): "
if /i "!UNINSTALL_QQ!"=="y" (
    echo Uninstalling current QQ...
    if exist "%LOCALAPPDATA%\Programs\Tencent\QQNT\Uninstall QQNT.exe" (
        "%LOCALAPPDATA%\Programs\Tencent\QQNT\Uninstall QQNT.exe" /S
    ) else if exist "C:\Program Files\Tencent\QQNT\Uninstall QQNT.exe" (
        "C:\Program Files\Tencent\QQNT\Uninstall QQNT.exe" /S
    ) else (
        echo [NOTE] Auto-uninstaller not found. Please uninstall QQ manually in Control Panel.
        pause
        exit /b 1
    )
    timeout /t 3 >nul
    echo [OK] QQ uninstalled
)

set QQ_URL=https://qqdl.gtimg.cn/qqfile/QQNT/9.9.32/release/9d4083e2/QQ_9.9.32_260716_x64_01.exe
set QQ_FILE=%TEMP%\QQ_NT_x64.exe

if exist "%QQ_FILE%" (
    echo [SKIP] Installer already exists
) else (
    echo Downloading QQ NT (~300MB, please wait)...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%QQ_URL%' -OutFile '%QQ_FILE%' -UseBasicParsing}"
    if !errorLevel! neq 0 (
        echo [ERROR] QQ download failed, check your network
        pause
        exit /b 1
    )
)
echo Installing QQ NT...
"%QQ_FILE%" /S
if !errorLevel! neq 0 (
    echo [NOTE] QQ installer launched. Please complete the installation in the UI.
    pause
)
echo [OK] QQ NT installed
echo.

:: -- Install Python dependencies --
echo [4/5] Installing Python packages...
echo Installing ncatbot flask waitress...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install ncatbot flask waitress
if !errorLevel! neq 0 (
    echo [ERROR] Python dependencies failed. Try manually:
    echo        pip install ncatbot flask waitress
    pause
    exit /b 1
)
echo [OK] Python dependencies installed
echo.

:: -- Done --
echo [5/5] Installation complete!
echo.
echo +============================================================+
echo   Next steps:
echo.
echo   1. Edit config.yaml, fill in bot_qq / admin_qq
echo.
echo   2. Open MAA - Settings - Remote Control, enter:
echo      Task endpoint:  http://127.0.0.1:2345/maa/getTask
echo      Report endpoint: http://127.0.0.1:2345/maa/reportStatus
echo.
echo   3. Run: python maabot.py
echo      First run will auto-download NapCat and show QQ login.
echo      Scan QR code with the bot QQ account.
echo.
echo   4. Send a message to the bot (e.g. "help") to activate.
echo +============================================================+
echo.
pause
