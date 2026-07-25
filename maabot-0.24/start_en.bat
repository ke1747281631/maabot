@echo off
set PYTHONPATH=%PYTHONPATH%;%cd%
echo ========================================
echo   MAABot Launcher
echo ========================================
echo.
echo [1/1] Starting MAABot (standalone mode)...
python maabot.py --no-qqbot

pause
