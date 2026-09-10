@echo off
title ClaudioUi
cd /d "%~dp0"

echo Checking for existing server on port 8765...
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8765 "') do (
    echo   Stopping old server PID %%p...
    taskkill /PID %%p /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo Starting ClaudioUi...
start /min "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File "%~dp0claudioui_server.ps1"
exit
if errorlevel 1 (
    echo.
    echo ERROR: Server exited with an error. Check claudioui.log for details.
    pause
)
