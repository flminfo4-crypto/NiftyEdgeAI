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

REM --- 3. Start the server -------------------------------------------
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
