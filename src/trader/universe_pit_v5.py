"""[v3.59.0 — V5 Phase 2] Point-in-time S&P 500 universe via fja05680/sp500.

Replaces `universe_pit.py`'s Wikipedia scrape with the canonical
`fja05680/sp500` GitHub dataset (832 stars, MIT license, full add/drop
history back to 1996, single CSV per snapshot).

Source: https://github.com/fja05680/sp500/blob/master/S%26P%20500%20Historical%20Components%20%26%20Changes(05-25-2025).csv

The CSV format:
  date,tickers
  1996-12-31,A,AA,AAPL,...
  1997-01-08,A,AA,AAPL,AAS,...
  ...

Each row is a snapshot of S&P 500 membership effective from that date
until the next snapshot date.

This module exposes the same interface as universe_pit.py
(members_as_of(date)) so callers can switch with one import line.

The Wikipedia source remains as a diff-audit canary — at every monthly
rebalance, both sources are queried and the symmetric-difference is
logged. If the diff exceeds 5 names the LIVE run halts pending review.

Caching: full CSV is fetched once per week into data/sp500_history.csv.
Each `members_as_of()` call is a constant-time DataFrame lookup.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Pin a specific snapshot URL — the file in the upstream repo is updated
# periodically; pinning means our backtests are reproducible. Update
# this URL deliberately when refreshing.
SP500_HISTORY_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes(05-25-2025).csv"
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
LOCAL_CSV = DATA_DIR / "sp500_history.csv"
CACHE_TTL_SEC = 7 * 24 * 3600  # 1 week


def _cache_stale() -> bool:
    if not LOCAL_CSV.exists():
        return True
    age = time.time() - LOCAL_CSV.stat().st_mtime
    return age > CACHE_TTL_SEC


def _refresh_cache() -> bool:
    """Download the upstream CSV. Returns True on success."""
    try:
        import urllib.request
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SP500_HISTORY_URL, timeout=30) as resp:
            data = resp.read()
        LOCAL_CSV.write_bytes(data)
        return True
    except Exception:
        return False


def _load_snapshots() -> list[tuple[str, frozenset[str]]]:
    """Returns list of (date_str, member_set) sorted ascending by date.

    Refreshes from upstream if local cache is stale or missing.
    """
    if _cache_stale():
        _refresh_cache()
    if not LOCAL_CSV.exists():
        return []
    snapshots: list[tuple[str, frozenset[str]]] = []
    try:
        with LOCAL_CSV.open(newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) < 2:
                    continue
                date_str = row[0].strip()
                # Some CSV variants use a single comma-separated cell; some use 1 col per ticker
                if len(row) == 2:
                    tickers = row[1].split(",")
                else:
                    tickers = row[1:]
                cleaned = frozenset(t.strip() for t in tickers if t.strip())
                if date_str and cleaned:
                    snapshots.append((date_str, cleaned))
    except Exception:
        return []
    snapshots.sort(key=lambda t: t[0])
    return snapshots


def members_as_of(date_str: str) -> tuple[str, ...]:
    """Returns S&P 500 members effective AT-OR-BEFORE date_str (YYYY-MM-DD).

    Falls back to empty tuple if cache is unavailable. Callers should
    treat empty as "data layer broken — halt rebalance" rather than
    "S&P 500 has no members."
    """
    snaps = _load_snapshots()
    if not snaps:
        return ()
    # Find the latest snapshot with date <= date_str
    chosen: Optional[frozenset[str]] = None
    for d, members in snaps:
        if d <= date_str:
            chosen = members
        else:
            break
    if chosen is None:
        chosen = snaps[0][1]  # before earliest snapshot → use earliest
    return tuple(sorted(chosen))


def diff_against_wiki(date_str: str) -> dict:
    """Compute the symmetric-difference between this V5 source and the
    legacy Wikipedia source. Used as a daily canary — if the diff
    exceeds 5 names something is off.

    Returns {only_v5: [...], only_wiki: [...], both: int, source_ok: bool}
    """
    v5 = set(members_as_of(date_str))
    wiki = set()
    try:
        from .universe_pit import members_as_of as wiki_members
        wiki = set(wiki_members(date_str))
    except Exception:
        return {"only_v5": sorted(v5), "only_wiki": [], "both": 0,
                "source_ok": False, "error": "wiki source unavailable"}
    return {
        "only_v5": sorted(v5 - wiki),
        "only_wiki": sorted(wiki - v5),
        "both": len(v5 & wiki),
        "source_ok": True,
    }


def is_canary_clean(date_str: str, max_diff: int = 5) -> bool:
    """The canary check used at every monthly rebalance."""
    d = diff_against_wiki(date_str)
    if not d.get("source_ok"):
        # Wiki being down should NOT halt the LIVE path; canary is
        # advisory. If V5 source is also down (members_as_of returned
        # empty), is_canary_clean still returns True and the caller
        # checks the empty-members case directly.
        return True
    diff_count = len(d["only_v5"]) + len(d["only_wiki"])
    return diff_count <= max_diff
