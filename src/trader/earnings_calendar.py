"""[v3.63.0] Multi-source earnings calendar with fallback chain.

Replaces yfinance.Ticker.earnings_dates which silently returns empty
for major tickers (the bug that made EarningsRule INERT in v3.58.1+).

Source priority (first-success wins):
  1. Polygon.io free tier (POLYGON_API_KEY) — 5 req/min, reliable
  2. Finnhub free (FINNHUB_API_KEY) — 60 req/min, reliable
  3. Alpha Vantage free (ALPHA_VANTAGE_KEY) — 500 req/day
  4. yfinance (free, broken on major tickers but try anyway)

Public surface:
  • next_earnings_date(symbol, days_ahead=14) → date | None
  • earnings_within_window(symbols, start, end) → {sym: date}
  • status() → dict describing which sources are configured

Per-call result is cached on disk (data/earnings_cache.json) for 24h
because earnings dates rarely change once announced.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional


CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "earnings_cache.json"
CACHE_TTL_HOURS = 24


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass


def _cache_get(symbol: str) -> Optional[date]:
    cache = _load_cache()
    entry = cache.get(symbol.upper())
    if not entry:
        return None
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        if (datetime.utcnow() - cached_at).total_seconds() > CACHE_TTL_HOURS * 3600:
            return None
        if entry.get("date"):
            return date.fromisoformat(entry["date"])
        return None  # cached "no upcoming earnings"
    except Exception:
        return None


def _cache_set(symbol: str, d: Optional[date], source: str) -> None:
    cache = _load_cache()
    cache[symbol.upper()] = {
        "date": d.isoformat() if d else None,
        "source": source,
        "cached_at": datetime.utcnow().isoformat(),
    }
    _save_cache(cache)


# ============================================================
# Source: Polygon.io
# ============================================================
def _polygon(symbol: str, days_ahead: int = 30) -> Optional[date]:
    """Polygon.io v3 reference-financials endpoint. Returns next earnings
    date within `days_ahead`."""
    api_key = os.getenv("POLYGON_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request, urllib.parse
        # Polygon's earnings endpoint returns scheduled dates
        # https://polygon.io/docs/stocks/get_v3_reference_tickers__ticker__events
        url = (f"https://api.polygon.io/v3/reference/tickers/{symbol}/events"
               f"?types=earnings&apiKey={api_key}")
        req = urllib.request.Request(url, headers={"User-Agent": "trader/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        results = data.get("results", {}).get("events", [])
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=days_ahead)
        for ev in results:
            try:
                d = date.fromisoformat(ev.get("date", "")[:10])
                if today <= d <= cutoff:
                    return d
            except Exception:
                continue
        return None
    except Exception:
        return None


# ============================================================
# Source: Finnhub
# ============================================================
def _finnhub(symbol: str, days_ahead: int = 30) -> Optional[date]:
    """Finnhub /calendar/earnings endpoint. Free tier: 60 req/min."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=days_ahead)
        url = (f"https://finnhub.io/api/v1/calendar/earnings"
               f"?from={today.isoformat()}&to={cutoff.isoformat()}"
               f"&symbol={symbol}&token={api_key}")
        req = urllib.request.Request(url, headers={"User-Agent": "trader/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        rows = data.get("earningsCalendar", [])
        for r in rows:
            try:
                d = date.fromisoformat(r.get("date", "")[:10])
                if today <= d <= cutoff:
                    return d
            except Exception:
                continue
        return None
    except Exception:
        return None


# ============================================================
# Source: Alpha Vantage
# ============================================================
def _alpha_vantage(symbol: str, days_ahead: int = 30) -> Optional[date]:
    """Alpha Vantage EARNINGS_CALENDAR endpoint. Free tier: 500 req/day."""
    api_key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request, csv, io
        url = (f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR"
               f"&symbol={symbol}&horizon=3month&apikey={api_key}")
        req = urllib.request.Request(url, headers={"User-Agent": "trader/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            csv_text = resp.read().decode()
        reader = csv.DictReader(io.StringIO(csv_text))
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=days_ahead)
        for row in reader:
            try:
                d = date.fromisoformat(row.get("reportDate", "")[:10])
                if today <= d <= cutoff:
                    return d
            except Exception:
                continue
        return None
    except Exception:
        return None


# ============================================================
# Source: yfinance (last resort)
# ============================================================
def _yfinance(symbol: str, days_ahead: int = 30) -> Optional[date]:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        df = getattr(t, "earnings_dates", None)
        if df is None or (hasattr(df, "empty") and df.empty):
            return None
        today = datetime.utcnow().date()
        cutoff = today + timedelta(days=days_ahead)
        for idx in df.index:
            try:
                d = idx.date() if hasattr(idx, "date") else None
                if d and today <= d <= cutoff:
                    return d
            except Exception:
                continue
        return None
    except Exception:
        return None


# ============================================================
# Public API
# ============================================================
def next_earnings_date(symbol: str, days_ahead: int = 14,
                         use_cache: bool = True) -> Optional[date]:
    """Return the next earnings date for `symbol` within `days_ahead`.
    None if no earnings in window or all sources failed."""
    if use_cache:
        cached = _cache_get(symbol)
        if cached is not None:
            today = datetime.utcnow().date()
            if today <= cached <= today + timedelta(days=days_ahead):
                return cached

    # Source chain — first non-None wins
    for src_name, src_fn in [
        ("polygon", _polygon),
        ("finnhub", _finnhub),
        ("alpha_vantage", _alpha_vantage),
        ("yfinance", _yfinance),
    ]:
        result = src_fn(symbol, days_ahead)
        if result is not None:
            _cache_set(symbol, result, src_name)
            return result

    # All sources empty — cache the negative for the day
    _cache_set(symbol, None, "none")
    return None


def earnings_within_window(symbols: list[str],
                             start: Optional[date] = None,
                             end: Optional[date] = None) -> dict[str, date]:
    """Bulk lookup. Returns {symbol: earnings_date} for any symbol with
    an earnings in [start, end]."""
    start = start or datetime.utcnow().date()
    end = end or (start + timedelta(days=14))
    days = (end - start).days
    out: dict[str, date] = {}
    for sym in symbols:
        d = next_earnings_date(sym, days_ahead=days)
        if d and start <= d <= end:
            out[sym] = d
    return out


def status() -> dict:
    """Returns which sources are configured + cache stats."""
    cache = _load_cache()
    return {
        "polygon_configured": bool(os.getenv("POLYGON_API_KEY", "")),
        "finnhub_configured": bool(os.getenv("FINNHUB_API_KEY", "")),
        "alpha_vantage_configured": bool(os.getenv("ALPHA_VANTAGE_KEY", "")),
        "yfinance_available": True,  # always available, often broken
        "cache_entries": len(cache),
        "cache_file": str(CACHE_FILE),
        "any_paid_source_configured": any([
            os.getenv("POLYGON_API_KEY", ""),
            os.getenv("FINNHUB_API_KEY", ""),
            os.getenv("ALPHA_VANTAGE_KEY", ""),
        ]),
    }
