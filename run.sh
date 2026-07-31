#!/usr/bin/env bash
# NiftyEdgeAI - one-click dev launcher (macOS/Linux)
# Installs broker-plugins + ai-engine + backend deps (editable), then starts
# the FastAPI backend on http://localhost:8000 using broker-plugins/mock
# (no live broker / market data needed).
set -e
cd "$(dirname "$0")"

echo "=== Installing broker-plugins (editable) ==="
pip install -e ./broker-plugins

echo "=== Installing ai-engine (editable) ==="
pip install -e ./ai-engine

echo "=== Installing backend requirements ==="
pip install -r ./backend/requirements.txt

echo
echo "=== Starting backend on http://localhost:8000 ==="
echo "API docs:  http://localhost:8000/api/v1/docs"
echo "Dashboard: open frontend/index.html in your browser once the server below says 'Application startup complete'"
echo "Press CTRL+C to stop the server."
echo

cd backend
uvicorn app.main:app --reload --port 8000
