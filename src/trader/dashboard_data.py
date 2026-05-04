"""Cross-cutting data helpers extracted from scripts/dashboard.py
(v3.67.0 split).

Holds the four data-layer helpers that every view uses:
- `query` — read-only SQLite query, gracefully handles missing tables
- `read_state_file` — read JSON state file, returns {} on miss
- `live_portfolio` — Alpaca broker fetch wrapper with safe error class
- `cached_snapshots` — last 30 daily_snapshot rows

View-specific cached helpers (`_cached_brinson`, `_cached_events`, etc.)
stay co-located with their view function in dashboard.py — moving them
here would break the locality benefit without cleanup payoff.

All helpers are decorated with `@st.cache_data` so Streamlit handles
the per-call TTL. Cache keys are tied to function qualname; moving
these to a new module RESETS the cache (one-time, harmless).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


@st.cache_data(ttl=10)
def query(path_str: str, sql: str, params: tuple = (),
           silent: bool = False) -> pd.DataFrame:
    """Read-only SQLite query.

    `silent=True` suppresses st.error display when the caller is
    handling missing-table / empty-table cases gracefully (e.g.,
    querying slippage_log before any orders have been placed).
    Always silent for "no such table" — those are expected for
    tables created lazily."""
    if not Path(path_str).exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(f"file:{path_str}?mode=ro", uri=True) as c:
            return pd.read_sql_query(sql, c, params=params)
    except Exception as e:
        msg = str(e)
        if "no such table" in msg.lower():
            return pd.DataFrame()
        if not silent:
            st.error(f"query failed: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=10)
def read_state_file(path_str: str) -> dict:
    """Read a JSON state file. Returns {} on missing or unparseable."""
    p = Path(path_str)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


@st.cache_data(
    ttl=60,
    show_spinner="📡 Fetching live positions from broker...",
)
def live_portfolio():
    """Alpaca broker fetch wrapped in error-class fallback so views can
    do `live.error` checks rather than try/except."""
    try:
        from trader.positions_live import fetch_live_portfolio
        return fetch_live_portfolio()
    except Exception as e:
        class E:
            error = f"{type(e).__name__}: {e}"
            equity = None
            cash = None
            buying_power = None
            total_unrealized_pl = 0
            total_day_pl_dollar = 0
            total_day_pl_pct = None
            positions = []
            timestamp = datetime.utcnow().isoformat()
        return E()


@st.cache_data(ttl=30, show_spinner=False)
def cached_snapshots(db_path: str):
    """Last 30 rows of daily_snapshot, newest first."""
    return query(
        db_path,
        "SELECT * FROM daily_snapshot ORDER BY date DESC LIMIT 30",
    )
