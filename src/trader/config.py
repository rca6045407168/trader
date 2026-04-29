"""Env config. Single source of truth — every other module imports from here."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
# override=True so .env beats any stale shell env (esp. an empty ANTHROPIC_API_KEY).
load_dotenv(ROOT / ".env", override=True)

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

TOP_N = int(os.getenv("TOP_N", "3"))  # v3.6 fix — was 5; LIVE variant is top-3 at 80%
LOOKBACK_MONTHS = int(os.getenv("LOOKBACK_MONTHS", "12"))
USE_REGIME_FILTER = os.getenv("USE_REGIME_FILTER", "false").lower() == "true"
USE_DEBATE = os.getenv("USE_DEBATE", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
# v3.28: production universe selection.
#   "liquid_50" — hand-picked top-50 mega-caps (default, matches v3.x backtest survivor universe)
#   "sp500"     — full current S&P 500 (~500 names; PIT-aligned with v3.8+ honest baseline)
# Flip to "sp500" to align production with the PIT-honest baseline (+0.96 Sharpe).
# Test the comparison via: python scripts/compare_live_universes.py
LIVE_UNIVERSE = os.getenv("LIVE_UNIVERSE", "liquid_50").lower()
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "claude-sonnet-4-6")
POSTMORTEM_MODEL = os.getenv("POSTMORTEM_MODEL", "claude-opus-4-7")
