"""Insider buying signal — Cohen-Malloy-Pomorski 2012 style.

Long-only cross-sectional signal: rank universe by recent net insider
buying, take the top-N. Historical edge: ~3 %/yr alpha vs SPY for
cluster-buy portfolios (Cohen-Malloy-Pomorski 2012); the long-only
slice we use is closer to 1-2 %/yr after costs, decaying since the
2012 publication per McLean-Pontiff.

Data source: yfinance's `Ticker.insider_purchases` (a 6-month
aggregate of insider transactions). This is coarser than the
academic spec (which used 30-day rolling Form 4 windows), so the
expected edge is lower — but the operational simplicity is high,
and the signal direction is correct.

For a future v7 upgrade, swap the data source to direct SEC EDGAR
Form 4 XML parsing — that gives transaction-level granularity at
the cost of ~1-2 min of fetch time per daily run.

Caching: every fetch is cached in `data/insider_cache.parquet` keyed
by ticker. yfinance is rate-limited and the underlying data updates
slowly (Form 4s have a 2-day reporting deadline; aggregates roll
monthly), so a 24-hour TTL is plenty.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "insider_cache.parquet"
CACHE_TTL_HOURS = 24


def _read_cache() -> pd.DataFrame:
    if CACHE_PATH.exists():
        try:
            return pd.read_parquet(CACHE_PATH)
        except Exception:
            return pd.DataFrame(columns=["ticker", "score", "net_shares",
                                           "fetched_at"])
    return pd.DataFrame(columns=["ticker", "score", "net_shares",
                                   "fetched_at"])


def _write_cache(df: pd.DataFrame) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)


def _fetch_one(ticker: str, yf_module=None) -> Optional[dict]:
    """Pull yfinance insider aggregate for one ticker. Returns None
    on any failure (defensive — the upstream API is brittle)."""
    if yf_module is None:
        import yfinance as yf_module
    try:
        df = yf_module.Ticker(ticker).insider_purchases
    except Exception:
        return None
    if df is None or df.empty:
        return None
    try:
        # The DataFrame has rows labeled "Net Shares Purchased (Sold)"
        # and "% Net Shares Purchased (Sold)". We pull both — the
        # percentage is the cross-sectional comparable; the share
        # count is for sanity-checking.
        first_col = df.columns[0]
        net_row = df[df[first_col].astype(str).str.contains(
            "Net Shares", na=False, regex=False,
        )]
        pct_row = df[df[first_col].astype(str).str.contains(
            "% Net Shares", na=False, regex=False,
        )]
        net = float(net_row.iloc[0, 1]) if not net_row.empty else 0.0
        pct = float(pct_row.iloc[0, 1]) if not pct_row.empty else 0.0
    except Exception:
        return None
    return {"net_shares": net, "score": pct}


def insider_scores(universe: list[str], yf_module=None,
                    cache_ttl_hours: float = CACHE_TTL_HOURS,
                    cache_path: Optional[Path] = None) -> dict[str, float]:
    """Score every ticker in `universe` by net insider buying.

    Higher score = more insider conviction. Scores are the
    `% Net Shares Purchased (Sold)` field from yfinance — a
    signed fraction interpretable across tickers (positive = net
    buying, negative = net selling).

    Cache is keyed by ticker; entries older than `cache_ttl_hours`
    are refetched. Missing/failed tickers are silently dropped from
    the output (caller can detect by intersecting universe with
    returned keys).
    """
    if cache_path is not None:
        # Local override for testing — bind read/write to the supplied path
        _local_path = cache_path

        def _read():
            if _local_path.exists():
                try:
                    return pd.read_parquet(_local_path)
                except Exception:
                    return pd.DataFrame(columns=["ticker", "score",
                                                    "net_shares", "fetched_at"])
            return pd.DataFrame(columns=["ticker", "score", "net_shares",
                                           "fetched_at"])

        def _write(df):
            _local_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(_local_path, index=False)
    else:
        _read = _read_cache
        _write = _write_cache

    cache = _read()
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=cache_ttl_hours)
    rows = []
    out: dict[str, float] = {}

    for sym in universe:
        # Try cache first
        if not cache.empty and "ticker" in cache.columns:
            existing = cache[cache["ticker"] == sym]
            if not existing.empty:
                fetched = pd.to_datetime(existing.iloc[0]["fetched_at"])
                if fetched >= cutoff:
                    out[sym] = float(existing.iloc[0]["score"])
                    rows.append(existing.iloc[0].to_dict())
                    continue
        # Cache miss — fetch fresh
        data = _fetch_one(sym, yf_module=yf_module)
        if data is None:
            continue
        rows.append({
            "ticker": sym,
            "score": data["score"],
            "net_shares": data["net_shares"],
            "fetched_at": now.isoformat(),
        })
        out[sym] = data["score"]
        # Be polite to yfinance — small sleep between fetches
        time.sleep(0.1)

    # Merge stale rows for tickers we didn't refetch this call
    if not cache.empty and "ticker" in cache.columns:
        seen = {r["ticker"] for r in rows}
        stale = cache[~cache["ticker"].isin(seen)]
        if not stale.empty:
            rows.extend(stale.to_dict("records"))

    if rows:
        _write(pd.DataFrame(rows))

    return out


def top_n_by_insider(universe: list[str], n: int = 10,
                       min_score: float = 0.0,
                       yf_module=None,
                       cache_path: Optional[Path] = None) -> list[tuple[str, float]]:
    """Return top-N tickers by insider score, filtered to score >= min_score.

    `min_score=0.0` means "only positive net buying" — i.e., we won't
    pick a name where insiders are net sellers.
    """
    scores = insider_scores(universe, yf_module=yf_module,
                              cache_path=cache_path)
    ranked = [(t, s) for t, s in scores.items() if s >= min_score]
    ranked.sort(key=lambda x: -x[1])
    return ranked[:n]
