# NiftyEdge — Story 0: Setup & Dhan Connection

What you get after this story: the app starts with one command, shows a
green "API Connected" dot when your Dhan token works, and lists the
security IDs of NIFTY, BANKNIFTY, FINNIFTY, SENSEX and India VIX.

## Prerequisites (one-time)

1. **Python 3.12+** — https://www.python.org/downloads/ (tick "Add to PATH" during install)
2. **Node.js 20+** — https://nodejs.org (LTS installer)
3. **Dhan API credentials** — log in at https://web.dhan.co →
   *My Profile → DhanHQ Trading APIs* → generate an **Access Token**.
   Note your **Client ID** and the token. Data APIs must be active.

## Setup (10 minutes)

Open a terminal (cmd) in this folder, then:

```bat
:: 1. Backend — create a virtual environment and install packages
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 2. Credentials — copy the example and edit it
copy .env.example .env
notepad .env        (paste your Client ID and Access Token, save, close)

:: 3. Frontend — install packages
cd ..\frontend
npm install
cd ..
```

## Run

```bat
run.bat
```

Two windows open (backend + frontend). Then open **http://localhost:3000**.

## What you should see (= Story 0 acceptance check)

| Check | Expected |
|---|---|
| Page loads | "NiftyEdge" heading within 3 seconds |
| Dhan dot | **Green** "Connected". If red, the message tells you exactly what to fix |
| Heartbeat | Timestamp updating every 5 seconds (proves the live WebSocket works) |
| Core Instruments | 5 rows: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, INDIA VIX with security IDs |

## Troubleshooting

- **Red dot, "token rejected/expired"** → generate a fresh Access Token on
  web.dhan.co, update `backend\.env`, restart `run.bat`. Dhan tokens expire
  (typically every 24h unless you enabled a longer validity).
- **"Backend not reachable"** → check the backend window for errors;
  most common cause is the venv not activated or packages not installed.
- **Instruments table empty** → first start downloads Dhan's instrument
  master (~ a few MB); wait ~30 s and refresh. Corporate networks that
  block `images.dhan.co` will cause this.
- **Port already in use** → close other apps using 8000/3000 or edit the
  ports in `run.bat` and `frontend/vite.config.ts`.

## Security notes

- Credentials live only in `backend\.env` — ignored by git, never logged,
  never sent to the browser.
- This tool is analytics-only: it contains no order-placement code.

## Project layout

```
niftyedge/
  run.bat                 one-command start
  backend/
    app/main.py           FastAPI app + endpoints
    app/config.py         .env loading
    app/dhan_client.py    Dhan SDK wrapper + token verification
    app/instruments.py    instrument master download / search / index IDs
    app/ws_relay.py       internal WebSocket (heartbeat now, ticks in Story 1)
  frontend/
    src/App.tsx           status page (green/red dot, instruments table)
```

Next: **Story 1 — Live Price Engine** (streaming ticks for all 4 indices + VIX,
1-min bars, auto-reconnect, backfill).
