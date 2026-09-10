@echo off
setlocal enabledelayedexpansion
title ClaudioUI
cd /d "%~dp0"

echo ClaudioUI launcher
echo ===================
echo.

rem Every check below captures %errorlevel% into a variable immediately after
rem the command, then tests the variable - never a bare `if errorlevel N`.
rem Bare `if errorlevel` can misread stale state when it follows a command
rem that uses `>` redirection inside a parenthesized block (a real, reproduced
rem cmd.exe quirk - not hypothetical) - capturing immediately avoids that class
rem of bug entirely, including in code added here later.

rem --- 1. Python ------------------------------------------------------------
where python >nul 2>&1
set "RC=!errorlevel!"
if not "!RC!"=="0" (
    echo Python not found - installing via winget, this happens once...
    winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo ERROR: Python install failed.
        echo Install Python 3.10+ manually from https://python.org and re-run this script.
        pause
        exit /b 1
    )
    call :RefreshPath
    where python >nul 2>&1
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo Python was installed but isn't visible in this window yet.
        echo Please close this window and double-click Launch ClaudioUi.bat again.
        pause
        exit /b 1
    )
)

rem --- 2. claude CLI ----------------------------------------------------------
where claude >nul 2>&1
set "RC=!errorlevel!"
if not "!RC!"=="0" (
    echo claude CLI not found - installing, this happens once...
    curl -fsSL https://claude.ai/install.cmd -o "%TEMP%\claude_install.cmd" && call "%TEMP%\claude_install.cmd" && del "%TEMP%\claude_install.cmd"
    call :RefreshPath
    where claude >nul 2>&1
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo claude CLI was installed but isn't visible in this window yet.
        echo Please close this window and double-click Launch ClaudioUi.bat again.
        pause
        exit /b 1
    )
)

rem --- 3. venv + pinned dependencies (first run only) --------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Setting up ClaudioUI for the first time - this happens once...
    python -m venv .venv
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo ERROR: Could not create the Python virtual environment.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r backend\requirements.txt
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo ERROR: Failed to install dependencies. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

rem --- 4. stop any stale instance already bound to the port --------------------
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8765 " ^| findstr LISTENING') do (
    echo Stopping an existing ClaudioUI instance ^(PID %%p^)...
    taskkill /PID %%p /F >nul 2>&1
)

rem --- 5. launch under the auto-restart watchdog --------------------------------
rem This window stays open on purpose - if something goes wrong, the error is
rem visible here instead of hidden in a background process. The auth check
rem (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL) happens inside Python at startup;
rem this script never touches that credential itself.
echo.
echo Starting ClaudioUI...
cd backend
"..\.venv\Scripts\python.exe" -m app.watchdog
set "EXITCODE=%errorlevel%"
cd ..

if not "%EXITCODE%"=="0" (
    echo.
    echo ClaudioUI stopped with an error. Check %USERPROFILE%\.claudioui\logs\app.log for details.
    pause
)
exit /b %EXITCODE%

:RefreshPath
rem Appends the persisted (registry) PATH rather than replacing the session's
rem current PATH outright - an installer only ever adds to the registry value,
rem so this picks up new entries without dropping anything already present
rem in this session that isn't in the registry.
for /f "skip=2 tokens=3*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SysPath=%%A %%B"
for /f "skip=2 tokens=3*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "UserPath=%%A %%B"
set "PATH=%PATH%;%SysPath%;%UserPath%"
goto :eof
