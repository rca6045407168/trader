"""Options-market signals beyond plain VIX level.

VIX is a single number — historically informative but slow. The term structure
of implied vol (VIX9D / VIX / VIX3M) reveals more:

  - Backwardation (VIX9D > VIX or VIX > VIX3M): Stress regime; near-term vol
    expected to be HIGHER than longer-dated. Historically marks bottoms /
    near-bottoms (panic localized to now).
  - Contango (VIX9D < VIX < VIX3M): Calm regime; vol expected to mean-revert
    UP. The "normal" state.

SKEW index measures the cost of OTM puts vs OTM calls — high SKEW = paying up
for tail-risk insurance. Spikes precede SOME selloffs but with high false-
positive rate (noisy).

All data from yfinance:
  ^VIX9D — 9-day implied vol (CBOE)
  ^VIX   — 30-day implied vol
  ^VIX3M — 3-month implied vol
  ^SKEW  — 30-day SPX OTM tail-risk index

Used by overlay variants in regime_stress_test.py / variants.py.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

import pandas as pd


@lru_cache(maxsize=64)
def _vol_cached(start_str: str, end_str: str) -> dict:
    """Fetch VIX term structure as a dict of (ticker -> tuple of (date, value))."""
    from .data import fetch_history
    out = {}
    for t in ("^VIX", "^VIX9D", "^VIX3M", "^SKEW"):
        try:
            df = fetch_history([t], start=start_str, end=end_str)
            if t in df.columns:
                s = df[t].dropna()
                out[t] = tuple((d, float(v)) for d, v in zip(s.index, s.values))
            else:
                out[t] = tuple()
        except Exception:
            out[t] = tuple()
    return out


def fetch_vol_term_structure(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Returns DataFrame indexed by date with columns: VIX9D, VIX, VIX3M, SKEW."""
    raw = _vol_cached(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    cols = {}
    for t, rows in raw.items():
        if not rows:
            continue
        cols[t.lstrip("^")] = pd.Series([v for _, v in rows], index=[d for d, _ in rows])
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).sort_index()
    return df


def vix_term_backwardation(term: pd.DataFrame, asof: pd.Timestamp) -> bool:
    """True if VIX9D > VIX (front-month backwardation = acute stress).

    Robust to missing VIX9D — returns False if data unavailable.
    """
    if term.empty:
        return False
    snap = term[term.index <= asof]
    if snap.empty:
        return False
    row = snap.iloc[-1]
    if "VIX9D" not in row or "VIX" not in row:
        return False
    if pd.isna(row["VIX9D"]) or pd.isna(row["VIX"]):
        return False
    return float(row["VIX9D"]) > float(row["VIX"])


def vix_3m_inversion(term: pd.DataFrame, asof: pd.Timestamp) -> bool:
    """True if VIX > VIX3M (term-structure inversion across 30d→90d).

    Slower signal than VIX9D > VIX but more reliable (fewer false positives).
    """
    if term.empty:
        return False
    snap = term[term.index <= asof]
    if snap.empty:
        return False
    row = snap.iloc[-1]
    if "VIX" not in row or "VIX3M" not in row:
        return False
    if pd.isna(row["VIX"]) or pd.isna(row["VIX3M"]):
        return False
    return float(row["VIX"]) > float(row["VIX3M"])


def skew_extreme(term: pd.DataFrame, asof: pd.Timestamp,
                 percentile_threshold: float = 0.95) -> bool:
    """True if SKEW is in top `percentile_threshold` of trailing-1-year history.

    Tail-risk pricing extreme. Has historically preceded some sell-offs but
    high false-positive rate. Use as supporting signal, not primary.
    """
    if term.empty or "SKEW" not in term.columns:
        return False
    snap = term[term.index <= asof]
    if len(snap) < 252:
        return False
    skew = snap["SKEW"].dropna()
    if len(skew) < 252:
        return False
    threshold = float(skew.iloc[-252:].quantile(percentile_threshold))
    return float(skew.iloc[-1]) >= threshold
