"""Macro signals: yield curve, credit spreads, TIPS breakeven.

These are LEADING indicators historically — they move 1-4 weeks before equity
stress (sometimes longer). Used as INPUTS to position-sizing overlays, NOT as
asset-class swap triggers (per v3.5 lesson: asset-class swaps fail at V-shape
recoveries).

Data sources:
  - FRED CSV endpoint (free, no auth, public series)
  - yfinance for ETF-based proxies (HYG/LQD ratio for credit spreads)

Why these signals:
  - T10Y2Y (10y - 2y curve): Inverts before every US recession since 1955.
    Steepening from inversion is a stress / pre-recession signal.
  - HYG/LQD price ratio: Drops when HY spreads widen relative to IG spreads
    (= risk-off). Leads SPY by 1-2 weeks in 2007, 2018-Q4, 2020, 2022.
  - VIX term structure (separate module vol_signals.py).

This module is read-only data fetchers. Decision logic lives in the variants.
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

import pandas as pd
import requests


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@lru_cache(maxsize=64)
def _fred_cached(series_id: str, cosd: str, coed: str) -> tuple:
    """Cache-friendly FRED fetch (returns tuple of (date, value) tuples)."""
    try:
        r = requests.get(FRED_CSV_URL,
                         params={"id": series_id, "cosd": cosd, "coed": coed},
                         timeout=15)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna()
        # FRED returns "." for missing values — coerce
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        return tuple((d, float(v)) for d, v in zip(df["date"], df["value"]))
    except Exception:
        return tuple()


def fetch_fred_series(series_id: str, start: pd.Timestamp,
                     end: pd.Timestamp) -> pd.Series:
    """Fetch a FRED series as a pandas Series indexed by date."""
    cosd = start.strftime("%Y-%m-%d")
    coed = end.strftime("%Y-%m-%d")
    rows = _fred_cached(series_id, cosd, coed)
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series([v for _, v in rows], index=[d for d, _ in rows], name=series_id)
    return s.sort_index()


def yield_curve_10y_2y(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """10y minus 2y Treasury yield. Negative = inverted = bearish leading indicator."""
    return fetch_fred_series("T10Y2Y", start, end)


def credit_spread_proxy(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """HYG/LQD price ratio. Drops when HY spreads widen vs IG → risk-off.

    Uses yfinance (no FRED truncation issue). Goes back to 2007 (HYG inception).
    """
    from .data import fetch_history
    try:
        prices = fetch_history(["HYG", "LQD"], start=start.strftime("%Y-%m-%d"),
                               end=end.strftime("%Y-%m-%d"))
        if "HYG" not in prices.columns or "LQD" not in prices.columns:
            return pd.Series(dtype=float)
        ratio = prices["HYG"] / prices["LQD"]
        return ratio.dropna()
    except Exception:
        return pd.Series(dtype=float)


def credit_spread_widening(ratio: pd.Series, lookback_days: int = 20,
                           threshold_sigma: float = 2.0) -> bool:
    """True if HYG/LQD ratio dropped >threshold_sigma over `lookback_days`.

    A drop is bad: HY spreads widening = risk-off. Use as a position-sizing
    cut signal (NOT an asset-class swap signal).
    """
    if len(ratio) < lookback_days + 60:
        return False
    recent = ratio.iloc[-lookback_days:]
    pct_change = float(recent.iloc[-1] / recent.iloc[0] - 1)
    # Compute trailing 252-day std of 20-day rolling pct changes for normalization
    rolling = ratio.pct_change(lookback_days).dropna()
    if len(rolling) < 60:
        return False
    sigma = float(rolling.iloc[-252:].std())
    if sigma <= 0:
        return False
    z = pct_change / sigma
    return z < -threshold_sigma  # negative z = widening = risk-off


def yield_curve_stress(curve: pd.Series, days_inverted: int = 60) -> bool:
    """True if curve has been inverted for ≥days_inverted recently AND is
    currently steepening (= classic recession-imminent signal).

    Inverted = curve < 0. Steepening = curve rising over last 20 days.
    """
    if len(curve) < days_inverted + 20:
        return False
    recent = curve.iloc[-days_inverted - 20:]
    inverted_count = int((recent.iloc[:-20] < 0).sum())
    last_20 = recent.iloc[-20:]
    steepening = float(last_20.iloc[-1]) > float(last_20.iloc[0])
    return inverted_count >= days_inverted and steepening
