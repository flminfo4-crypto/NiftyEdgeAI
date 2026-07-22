"""Loads settings from .env. Secrets never leave this machine."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Dhan instrument master (compact). Downloaded once per day.
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


def credentials_present() -> bool:
    return bool(DHAN_CLIENT_ID) and bool(DHAN_ACCESS_TOKEN)
