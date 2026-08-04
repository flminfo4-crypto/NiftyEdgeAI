@echo off
REM NiftyEdgeAI - one-click dev launcher (Windows)
REM Self-diagnosing: finds Python via the py launcher or python, uses
REM "python -m" for pip/uvicorn so nothing depends on PATH, logs everything,
REM and always pauses so you can read any error.
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================
echo  NiftyEdgeAI launcher
echo ============================================
echo.

REM --- 1. Find Python -------------------------------------------------
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo [ERROR] Python was not found on this computer.
    echo.
    echo Fix: install Python 3.10 or newer from https://www.python.org/downloads/
    echo IMPORTANT: on the first installer screen, tick the checkbox
    echo    "Add python.exe to PATH"
    echo then run this file again.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PY% --version') do echo Found %%v (command: %PY%)
echo.

REM --- 2. Install dependencies ---------------------------------------
echo === Installing broker-plugins (editable) ===
%PY% -m pip install -e .\broker-plugins --quiet || goto :piperror

echo === Installing ai-engine (editable) ===
%PY% -m pip install -e .\ai-engine --quiet || goto :piperror

echo === Installing backend requirements ===
%PY% -m pip install -r .\backend\requirements.txt --quiet || goto :piperror
echo All dependencies installed.
echo.

REM --- 3. Free port 8000 if a previous run is still occupying it -------
REM The most common cause: closing this window with the X instead of
REM pressing CTRL+C first leaves uvicorn running in the background, so the
REM NEXT run.bat hits "only one usage of each socket address is normally
REM permitted" and exits immediately.
set "EXISTING_PID="
for /f "usebackq" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess" 2^>nul`) do set "EXISTING_PID=%%p"

if defined EXISTING_PID (
    echo [WARNING] Port 8000 is already in use by process ID !EXISTING_PID!.
    echo This is usually a previous run of this launcher that wasn't closed
    echo properly - closing the window directly, instead of pressing
    echo CTRL+C first, can leave the server running in the background.
    echo.
    choice /C YN /M "Stop that process and continue"
    if errorlevel 2 (
        echo.
        echo Left it running. Close it yourself in Task Manager, or run:
        echo   taskkill /F /PID !EXISTING_PID!
        echo then run this file again.
        echo.
        pause
        exit /b 1
    )
    powershell -NoProfile -Command "Stop-Process -Id !EXISTING_PID! -Force" >nul 2>&1
    echo Stopped process !EXISTING_PID!. Waiting for the port to free up...
    timeout /t 2 /nobreak >nul
    set "EXISTING_PID="
    echo.
)

REM --- 4. Start the server -------------------------------------------
echo ============================================
echo  Starting backend on http://localhost:8000
echo  API docs:  http://localhost:8000/api/v1/docs
echo  Dashboard: open frontend\index.html or frontend\cpr-dashboard.html
echo             in your browser once you see "Application startup complete"
echo             (remember NIFTYEDGE_API_BASE should be http://localhost:8000/api/v1)
echo  Press CTRL+C in this window to stop the server.
echo ============================================
echo.

cd backend
%PY% -m uvicorn app.main:app --port 8000
echo.
echo Server stopped.
pause
exit /b 0

:piperror
echo.
echo [ERROR] Package installation failed - read the message above this line.
echo Common fixes:
echo   - No internet / proxy blocking pip: check your connection
echo   - Permission error: try again from a normal (non-admin) prompt
echo   - Corporate Python: try  %PY% -m pip install --user ...
echo.
pause
exit /b 1
