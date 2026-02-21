@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title MAA 通知机器人 - 一键安装

echo.
echo ╔══════════════════════════════════════════════╗
echo ║     MAA 通知机器人 - 一键安装脚本           ║
echo ║     安装内容：VC运行库 / 指定版QQ / NcatBot ║
echo ╚══════════════════════════════════════════════╝
echo.

:: ── 检查管理员权限 ──────────────────────────────
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] 请右键此脚本，选择"以管理员身份运行"
    pause
    exit /b 1
)

:: ── 检查 Python ──────────────────────────────────
echo [1/5] 检查 Python 环境...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9 或以上版本
    echo        下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python 已安装：%PY_VER%
echo.

:: ── 安装 VC++ 运行库 ────────────────────────────
echo [2/5] 安装 Visual C++ 运行库（NapCat 依赖）...
set VC_URL=https://aka.ms/vs/17/release/vc_redist.x64.exe
set VC_FILE=%TEMP%\vc_redist.x64.exe

if exist "%VC_FILE%" (
    echo [跳过] 安装包已存在，直接安装
) else (
    echo 正在下载 VC++ 运行库...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%VC_URL%' -OutFile '%VC_FILE%' -UseBasicParsing}"
    if !errorLevel! neq 0 (
        echo [错误] VC++ 运行库下载失败，请检查网络连接
        pause
        exit /b 1
    )
)
echo 正在安装 VC++ 运行库（静默安装，请稍候）...
"%VC_FILE%" /install /quiet /norestart
echo [OK] VC++ 运行库安装完成
echo.

:: ── 安装指定版本 QQ ─────────────────────────────
echo [3/5] 安装 QQ 9.9.26（NapCat 兼容版本）...
echo.
echo [提示] 当前版本 QQ（如已安装）需要先卸载，
echo        否则新版本安装可能失败或覆盖后不兼容。
echo.
set /p UNINSTALL_QQ="是否现在卸载当前 QQ？(y/n，直接回车跳过): "
if /i "!UNINSTALL_QQ!"=="y" (
    echo 正在卸载当前 QQ...
    :: 尝试常见卸载路径
    if exist "%LOCALAPPDATA%\Programs\Tencent\QQNT\Uninstall QQNT.exe" (
        "%LOCALAPPDATA%\Programs\Tencent\QQNT\Uninstall QQNT.exe" /S
    ) else if exist "C:\Program Files\Tencent\QQNT\Uninstall QQNT.exe" (
        "C:\Program Files\Tencent\QQNT\Uninstall QQNT.exe" /S
    ) else (
        echo [提示] 未找到自动卸载程序，请手动在控制面板卸载 QQ 后重新运行此脚本
        pause
        exit /b 1
    )
    timeout /t 3 >nul
    echo [OK] QQ 已卸载
)

set QQ_URL=https://dldir1.qq.com/qqfile/qq/QQNT/40d6045a/QQ9.9.26.44343_x64.exe
set QQ_FILE=%TEMP%\QQ9.9.26.44343_x64.exe

if exist "%QQ_FILE%" (
    echo [跳过] 安装包已存在，直接安装
) else (
    echo 正在下载 QQ 9.9.26（约 200MB，请稍候）...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%QQ_URL%' -OutFile '%QQ_FILE%' -UseBasicParsing}"
    if !errorLevel! neq 0 (
        echo [错误] QQ 下载失败，请检查网络连接
        pause
        exit /b 1
    )
)
echo 正在安装 QQ 9.9.26...
"%QQ_FILE%" /S
if !errorLevel! neq 0 (
    echo [提示] QQ 安装程序已启动，请按照安装界面完成安装后继续
    pause
)
echo [OK] QQ 9.9.26 安装完成
echo.

:: ── 安装 Python 依赖 ────────────────────────────
echo [4/5] 安装 Python 依赖包...
echo 正在安装 ncatbot flask waitress...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install ncatbot flask waitress
if !errorLevel! neq 0 (
    echo [错误] Python 依赖安装失败，请检查网络或手动运行：
    echo        pip install ncatbot flask waitress
    pause
    exit /b 1
)
echo [OK] Python 依赖安装完成
echo.

:: ── 完成提示 ────────────────────────────────────
echo [5/5] 安装完成！
echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║  接下来请按照教程完成以下步骤：                         ║
echo ║                                                          ║
echo ║  1. 用文本编辑器打开 maabot.py                          ║
echo ║     修改 CONFIG 中的 bot_qq / admin_qq / log_path       ║
echo ║                                                          ║
echo ║  2. 打开 MAA → 设置 → 远程控制，填入：                  ║
echo ║     任务获取端点：http://127.0.0.1:6000/maa/getTask      ║
echo ║     任务汇报端点：http://127.0.0.1:6000/maa/reportStatus ║
echo ║                                                          ║
echo ║  3. 运行程序：python maabot.py                          ║
echo ║     首次运行会自动下载 NapCat 并弹出 QQ 登录界面        ║
echo ║     用机器人 QQ 号扫码登录即可                          ║
echo ║                                                          ║
echo ║  4. 给机器人发一条消息（如「帮助」）激活通知推送        ║
echo ╚══════════════════════════════════════════════════════════╝
echo.
pause
