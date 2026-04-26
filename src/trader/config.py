"""Env config. Single source of truth — every other module imports from here."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "journal.db"

for d in (DATA_DIR, CACHE_DIR, REPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)

ALPACA_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")

TOP_N = int(os.getenv("TOP_N", "5"))
LOOKBACK_MONTHS = int(os.getenv("LOOKBACK_MONTHS", "12"))
USE_REGIME_FILTER = os.getenv("USE_REGIME_FILTER", "false").lower() == "true"
USE_DEBATE = os.getenv("USE_DEBATE", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "claude-sonnet-4-6")
POSTMORTEM_MODEL = os.getenv("POSTMORTEM_MODEL", "claude-opus-4-7")
