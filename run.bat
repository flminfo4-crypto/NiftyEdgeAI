@echo off
REM NiftyEdge - one-command startup (Windows)
REM Starts backend (port 8000) and frontend (port 3000).

cd /d "%~dp0"

echo [1/2] Starting backend...
start "NiftyEdge Backend" cmd /k "cd backend && .venv\Scripts\activate && uvicorn app.main:app --port 8000 --reload"

echo [2/2] Starting frontend...
start "NiftyEdge Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo NiftyEdge starting. Open http://localhost:3000 in your browser.
