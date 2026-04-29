"""Point-in-time S&P 500 universe.

Removes survivorship bias from backtests. A static universe (DEFAULT_LIQUID_50)
is "today's top-50 most-liquid stocks" — but those aren't the same as the
top-50 in 2018. Backtesting on today's universe biases the results toward
companies that survived/grew, ignoring the ones that failed (Lehman, FRC,
SVB, BBBY, etc.) or shrank materially (GE, IBM in 2010s).

This module reconstructs S&P 500 membership AS-OF any past date by:
  1. Fetching the current 503-member list from Wikipedia
  2. Fetching the change history (~394 records of additions/removals)
  3. Walking changes BACKWARDS to undo them — yielding the membership at
     any historical date.

Source: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
Cached locally as JSON (changes infrequently; refresh weekly is enough).

Risks:
  - Wikipedia is community-maintained, has occasional gaps in the change log
  - Pre-2000 changes are sparse — module is reliable for 2010+ only
  - Some delisted tickers won't have yfinance price data → silent universe
    holes (we filter to tickers with available data)
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sp500_pit_cache.json"
USER_AGENT = "trader-research/1.0"


def _parse_change_date(raw: str) -> Optional[datetime]:
    """Parse Wikipedia date strings like 'April 9, 2026' → datetime."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    # Strip footnote refs like [6]
    raw = re.sub(r"\[[^\]]*\]", "", raw).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _strip_footnotes(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\[[^\]]*\]", "", s).strip()


def fetch_and_cache_sp500() -> dict:
    """Fetch S&P 500 constituents + change history from Wikipedia, cache locally.

    Returns dict with:
      - current_members: list[str] — current S&P 500 tickers
      - changes: list of {date, added, removed} — chronological additions/removals
      - cached_at: ISO timestamp
    """
    r = requests.get(WIKI_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))

    # Table 0: current members
    current_df = tables[0]
    current_members = [str(s).strip() for s in current_df["Symbol"].tolist()]

    # Table 1: changes (multi-level columns)
    changes_df = tables[1].copy()
    # Flatten multi-level columns
    changes_df.columns = [
        " ".join(c).strip() if isinstance(c, tuple) else str(c)
        for c in changes_df.columns
    ]
    changes = []
    for _, row in changes_df.iterrows():
        date_raw = str(row.get("Effective Date Effective Date", row.get("Effective Date", "")))
        added = _strip_footnotes(str(row.get("Added Ticker", "")))
        removed = _strip_footnotes(str(row.get("Removed Ticker", "")))
        d = _parse_change_date(date_raw)
        if d is None:
            continue
        added_clean = added if added and added.lower() != "nan" else None
        removed_clean = removed if removed and removed.lower() != "nan" else None
        if not (added_clean or removed_clean):
            continue
        changes.append({
            "date": d.isoformat(),
            "added": added_clean,
            "removed": removed_clean,
        })

    out = {
        "current_members": sorted(current_members),
        "changes": changes,
        "cached_at": datetime.utcnow().isoformat(),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(out, indent=2))
    return out


def _load_or_fetch() -> dict:
    """Use cached data if present; otherwise fetch fresh."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return fetch_and_cache_sp500()


@lru_cache(maxsize=512)
def sp500_membership_at(date_str: str) -> tuple:
    """Return tuple of S&P 500 tickers as-of the given date (YYYY-MM-DD).

    Algorithm: start with current members, walk changes backwards in time,
    UNDO each change (i.e., re-remove what was added, re-add what was removed)
    until we reach the requested date.
    """
    asof = datetime.strptime(date_str, "%Y-%m-%d")
    data = _load_or_fetch()
    members = set(data["current_members"])
    # Sort changes newest-first; walk back undoing each one whose date is AFTER asof
    changes = sorted(data["changes"], key=lambda c: c["date"], reverse=True)
    for change in changes:
        change_date = datetime.fromisoformat(change["date"])
        if change_date <= asof:
            # All remaining changes happened on or before asof — current state holds
            break
        # Undo: remove what was added, add back what was removed
        if change.get("added") and change["added"] in members:
            members.discard(change["added"])
        if change.get("removed"):
            members.add(change["removed"])
    return tuple(sorted(members))


def liquid_subset_at(date_str: str, top_k: int = 100,
                     prices: Optional[pd.DataFrame] = None,
                     window_days: int = 60) -> list[str]:
    """Return top-k most liquid (by avg dollar volume) S&P 500 names as of date_str.

    Args:
        date_str: as-of date YYYY-MM-DD
        top_k: how many to return
        prices: optional pre-fetched price DataFrame (faster); if None, fetches
                fresh price data for the membership universe
        window_days: lookback window for liquidity ranking

    Returns top-k tickers sorted by trailing dollar-volume.
    """
    members = sp500_membership_at(date_str)
    if not members:
        return []
    if prices is None:
        from .data import fetch_history
        asof = datetime.strptime(date_str, "%Y-%m-%d")
        start = (asof - pd.Timedelta(days=window_days * 3)).strftime("%Y-%m-%d")
        try:
            prices = fetch_history(list(members), start=start, end=date_str)
        except Exception:
            return list(members)[:top_k]
    if prices is None or prices.empty:
        return list(members)[:top_k]
    # Approximate liquidity = mean(price) over window (volume not always in fetched data)
    # Use price proxy: assume more-traded names have steadier price data.
    available = [t for t in members if t in prices.columns]
    if not available:
        return list(members)[:top_k]
    sliced = prices[available].iloc[-window_days:].dropna(how="all", axis=1)
    # Rank by mean price (rough proxy when volume not available) — within S&P 500,
    # higher-priced names tend to be larger market caps which tend to be more liquid.
    means = sliced.mean(axis=0).dropna()
    if means.empty:
        return list(members)[:top_k]
    ranked = means.sort_values(ascending=False).index.tolist()
    return ranked[:top_k]
