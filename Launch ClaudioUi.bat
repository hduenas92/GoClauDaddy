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

rem --- 1. Python (version-gated, not just "is something on PATH") -------------
rem A machine can already have a Python on PATH that this app has never been
rem tested against - e.g. a brand-new release with no prebuilt wheels yet for
rem our dependencies (pydantic-core ships compiled wheels per Python version;
rem a too-new interpreter forces pip to compile from Rust source, which then
rem fails too - two build toolchains both behind a same-week Python release).
rem So this checks the actual version, not just presence, and always creates
rem the venv from a specifically-selected interpreter (`py -3.13`), never
rem whatever happens to be first on PATH.
set "PYMIN=10"
set "PYMAX=13"
set "PYEXE="

rem Exact point release for the direct-download fallback below. winget always
rem resolves "latest 3.13.x" on its own; python.org's installer URL needs a
rem specific version, so this needs bumping occasionally as 3.13.x moves on -
rem it only matters on machines without winget, and any 3.13.x satisfies the
rem PYMIN/PYMAX range check either way.
set "PYFALLBACK=3.13.7"

call :FindGoodPython
if not defined PYEXE (
    echo No compatible Python found - installing Python 3.13, this happens once...
    call :CheckNetwork "www.python.org"
    if not defined NETOK (
        echo.
        echo ERROR: Can't reach python.org to download the installer.
        echo Check your internet connection ^(or VPN, if this network requires one^) and try again.
        pause
        exit /b 1
    )

    where winget >nul 2>&1
    set "RC=!errorlevel!"
    if "!RC!"=="0" (
        winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
        set "RC=!errorlevel!"
    ) else (
        rem winget is an optional Windows component (App Installer) - not
        rem guaranteed present, and a real machine in this rollout didn't have
        rem it. Fall back to the official installer directly rather than
        rem requiring an OS feature this app doesn't control.
        echo winget isn't available here - downloading the installer directly...
        rem Match the same arch check the official claude CLI installer uses -
        rem python.org publishes a real arm64 build too, so this isn't the
        rem "assume everyone's on x64" mistake the winget-absence bug was.
        set "PYARCH=amd64"
        if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PYARCH=arm64"
        if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "PYARCH=arm64"
        set "PYINSTALLER=%TEMP%\python-installer.exe"
        curl -fsSL "https://www.python.org/ftp/python/%PYFALLBACK%/python-%PYFALLBACK%-%PYARCH%.exe" -o "!PYINSTALLER!"
        set "RC=!errorlevel!"
        if "!RC!"=="0" (
            rem Per-user install (no admin/UAC needed either way) with the py
            rem launcher and pip included, and added to PATH for this user.
            "!PYINSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1
            set "RC=!errorlevel!"
        )
        del "!PYINSTALLER!" >nul 2>&1
    )

    if not "!RC!"=="0" (
        echo.
        echo ERROR: Python install failed.
        echo Install Python 3.13 manually from https://python.org and re-run this script.
        pause
        exit /b 1
    )
    call :RefreshPath
    call :FindGoodPython
    if not defined PYEXE (
        echo.
        echo Python 3.13 was installed but isn't visible in this window yet.
        echo Please close this window and double-click Launch ClaudioUi.bat again.
        pause
        exit /b 1
    )
)

rem --- 2. claude CLI ------------------------------------------------------------
where claude >nul 2>&1
set "RC=!errorlevel!"
if not "!RC!"=="0" (
    echo claude CLI not found - installing, this happens once...
    call :CheckNetwork "claude.ai"
    if not defined NETOK (
        echo.
        echo ERROR: Can't reach claude.ai to download the installer.
        echo Check your internet connection ^(or VPN, if this network requires one^) and try again.
        pause
        exit /b 1
    )
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
) else (
    rem Already installed - self-update in place rather than leaving whatever
    rem version happens to be on this machine. The CLI owns its own update
    rem mechanism (checks its release channel, replaces its own binary) - that's
    rem more robust than this script trying to parse/compare version strings
    rem itself, and matches how the org's CaaS onboarding already expects
    rem people to keep the CLI current. Non-fatal: an offline network or a
    rem transient failure here shouldn't block using the version already
    rem installed.
    echo Checking for claude CLI updates...
    claude update >nul 2>&1
)

rem --- 3. venv + pinned dependencies (first run only) --------------------------
rem Completion is tracked by a marker file written only AFTER a successful
rem dependency install - not just ".venv exists" - because venv creation can
rem succeed while the pip install that follows still fails (exactly what
rem happened when a too-new Python triggered a doomed source build). Without
rem the marker, a retry would see ".venv\Scripts\python.exe" already present
rem and skip straight past setup into a launch with dependencies missing -
rem trading one clear error for a more confusing one. If the marker is
rem missing, wipe and recreate the venv from scratch rather than trying to
rem patch a possibly half-built one.
if not exist ".venv\.deps_ok" (
    echo Setting up ClaudioUI for the first time - this happens once...
    if exist ".venv" rmdir /s /q ".venv"
    !PYEXE! -m venv .venv
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo ERROR: Could not create the Python virtual environment.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    rem --only-binary is a failsafe, not the primary fix: even with the right
    rem Python version selected above, this guarantees a fast, readable pip
    rem error instead of ever again falling into a multi-minute Rust/cargo
    rem build that was only going to fail anyway.
    ".venv\Scripts\python.exe" -m pip install --quiet --only-binary=:all: -r backend\requirements.txt
    set "RC=!errorlevel!"
    if not "!RC!"=="0" (
        echo.
        echo ERROR: Failed to install dependencies. Check your internet connection and try again.
        echo If this keeps failing, send the output above - it usually names the exact package.
        pause
        exit /b 1
    )
    echo done > ".venv\.deps_ok"
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

:FindGoodPython
rem Sets PYEXE to a command that runs a Python whose version is known-compatible
rem (3.%PYMIN%-3.%PYMAX%), or leaves PYEXE undefined if none is found. Prefers
rem the Windows `py` launcher with an explicit version (py -3.13) over bare
rem `python`, because `python` resolves to whatever is first on PATH - which is
rem exactly what picked an unsupported 3.14 install on a real test machine.
set "PYEXE="
for /l %%V in (%PYMAX%,-1,%PYMIN%) do (
    if not defined PYEXE (
        py -3.%%V -c "exit()" >nul 2>&1
        if !errorlevel! equ 0 set "PYEXE=py -3.%%V"
    )
)
if defined PYEXE goto :eof

rem No versioned py launcher match - fall back to bare `python`, but only
rem if its actual version is in range (never assume; check it).
where python >nul 2>&1
if not !errorlevel! equ 0 goto :eof
set "PYMAJOR="
set "PYMINOR="
for /f "tokens=1,2" %%A in ('python -c "import sys;print(sys.version_info[0],sys.version_info[1])" 2^>nul') do (
    set "PYMAJOR=%%A"
    set "PYMINOR=%%B"
)
if not "!PYMAJOR!"=="3" goto :eof
if !PYMINOR! LSS %PYMIN% goto :eof
if !PYMINOR! GTR %PYMAX% goto :eof
set "PYEXE=python"
goto :eof

:CheckNetwork
rem Sets NETOK if the given host answers, so a missing/blocked network gives
rem a clear "check your connection" message up front instead of winget or
rem curl hanging or failing with a cryptic error partway through an install.
set "NETOK="
curl -s -o nul --max-time 5 "https://%~1" >nul 2>&1
if !errorlevel! equ 0 set "NETOK=1"
goto :eof

:RefreshPath
rem Appends the persisted (registry) PATH rather than replacing the session's
rem current PATH outright - an installer only ever adds to the registry value,
rem so this picks up new entries without dropping anything already present
rem in this session that isn't in the registry.
for /f "skip=2 tokens=3*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SysPath=%%A %%B"
for /f "skip=2 tokens=3*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "UserPath=%%A %%B"
set "PATH=%PATH%;%SysPath%;%UserPath%"
goto :eof
