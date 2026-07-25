@echo off
chcp 936 >nul 2>&1
set PYTHONPATH=%PYTHONPATH%;%cd%
echo ========================================
echo   MAABot 一键启动
echo ========================================
echo.
echo [1/1] 启动 MAABot (独立模式)...
python maabot.py --no-qqbot

pause
