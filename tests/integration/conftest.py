"""
Makes `app` (backend/) importable when running pytest from the repo root or
from tests/, without requiring the backend to be installed as a package.
broker_plugins and niftyedge_ai_engine are expected to be pip-installed
editable (see backend/README.md "Run it") — that's how the backend itself
resolves them too.
"""

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
