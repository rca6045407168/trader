"""Residual momentum: factor-orthogonal momentum signal.

Source: Blitz, Hanauer & Vidojevic (2020) + Blitz-Hanauer "Residual Momentum
Revisited" (Robeco/SSRN, June 2024). Independently replicated by Chen & Velikov
(Critical Finance Review, 2024).

Thesis: raw 12-1 momentum is contaminated by factor exposure (low-vol in 2020,
value in 2022, etc.) that mean-reverts. Stripping factor loadings via 36-month
OLS regression on Fama-French 5 factors yields the IDIOSYNCRATIC component —
the actual stock-specific signal that persists.

This explains why our v3.5/v3.7/v3.10 stress-cut overlays kept failing: the
mean-reversion is INSIDE the signal (factor-loaded names reverse), not in the
macro environment we kept trying to detect.

Net OOS Sharpe in Blitz-Hanauer 2024: 0.85-1.10 across regions including
2018-Q4 + 2022 bears. Replicated independently — passes our gate criterion
that single-paper claims must have at least one independent replication.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ff5_cache.csv"
USER_AGENT = "trader-research/1.0"


def fetch_ff5_factors() -> pd.DataFrame:
    """Fetch Fama-French 5-factor daily data from Ken French's library.

    Returns DataFrame indexed by date with columns:
      Mkt-RF, SMB, HML, RMW, CMA, RF (all in percent — divide by 100 for decimals)
    """
    if CACHE_PATH.exists():
        try:
            cached = pd.read_csv(CACHE_PATH, index_col=0, parse_dates=True)
            # Refresh if cache is more than 7 days old
            mtime = datetime.fromtimestamp(CACHE_PATH.stat().st_mtime)
            if (datetime.utcnow() - mtime).days < 7:
                return cached
        except Exception:
            pass

    r = requests.get(FF5_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        # The CSV inside has a name like F-F_Research_Data_5_Factors_2x3_daily.CSV
        names = zf.namelist()
        csv_name = [n for n in names if n.lower().endswith(".csv")][0]
        with zf.open(csv_name) as f:
            text = f.read().decode("latin-1")

    # The file has a multi-line header. Find the header line that starts with ","
    lines = text.split("\n")
    start_idx = None
    for i, line in enumerate(lines):
        if line.startswith(",Mkt-RF") or "Mkt-RF" in line.split(",")[1:2]:
            start_idx = i
            break
    if start_idx is None:
        raise ValueError("Could not parse FF5 CSV header")
    # Find end of data section (Ken French's files often have monthly data
    # appended after; we want only daily, where index is YYYYMMDD)
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i].strip()
        if not line or line.startswith("Annual") or line.startswith("Copyright"):
            end_idx = i
            break

    csv_text = "\n".join(lines[start_idx:end_idx])
    df = pd.read_csv(io.StringIO(csv_text))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={df.columns[0]: "date"})
    # Date is YYYYMMDD int
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date")
    # All values are percentages (e.g., 0.79 = 0.79%) — divide by 100
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce") / 100.0
    df = df.dropna(how="all")
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE_PATH)
    return df


@lru_cache(maxsize=4)
def get_ff5_aligned() -> pd.DataFrame:
    """Cached FF5 factors, aligned daily."""
    return fetch_ff5_factors()


def compute_residual_returns(stock_returns: pd.Series, ff5: pd.DataFrame,
                              regression_window_months: int = 36) -> pd.Series:
    """Run rolling OLS of (stock - rf) on (Mkt-RF, SMB, HML, RMW, CMA) over
    a 36-month rolling window, return the residual time series.

    Args:
        stock_returns: daily returns of the stock (decimal, not percent)
        ff5: FF5 factors DataFrame (with Mkt-RF, SMB, HML, RMW, CMA, RF cols)
        regression_window_months: rolling regression window (default 36 months)

    Returns:
        residual returns series aligned to stock_returns index
    """
    # Align dates
    common = stock_returns.index.intersection(ff5.index)
    if len(common) < regression_window_months * 21:
        return pd.Series(dtype=float)
    sr = stock_returns.loc[common]
    f = ff5.loc[common]
    excess = sr - f["RF"]
    factors = f[["Mkt-RF", "SMB", "HML", "RMW", "CMA"]].values

    n = len(excess)
    window = regression_window_months * 21
    residuals = np.full(n, np.nan)

    for i in range(window, n):
        X = factors[i - window:i]
        y = excess.iloc[i - window:i].values
        # OLS with intercept: prepend column of ones
        X_aug = np.column_stack([np.ones(X.shape[0]), X])
        # Solve normal equations: beta = (X'X)^-1 X'y
        try:
            beta, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        # Predict today's excess return given today's factor values
        today_factors = factors[i]
        pred = beta[0] + np.dot(beta[1:], today_factors)
        residuals[i] = float(excess.iloc[i] - pred)

    return pd.Series(residuals, index=excess.index)


def residual_momentum_score(prices: pd.DataFrame, ff5: pd.DataFrame,
                             as_of: pd.Timestamp,
                             lookback_months: int = 12,
                             skip_months: int = 1,
                             regression_window_months: int = 36) -> pd.Series:
    """For each ticker in prices, compute the cumulative SUM of residuals
    over the (lookback_months - skip_months) window ending at as_of - skip_months.

    Returns a Series indexed by ticker with the residual-momentum score
    (higher = stronger idiosyncratic momentum).
    """
    daily_rets = prices.pct_change().dropna(how="all")
    L = lookback_months * 21
    S = skip_months * 21

    scores = {}
    for ticker in prices.columns:
        sr = daily_rets[ticker].dropna()
        if len(sr) < (L + S + regression_window_months * 21):
            continue
        try:
            resid = compute_residual_returns(sr, ff5, regression_window_months)
        except Exception:
            continue
        if resid.empty:
            continue
        # Sum of residuals over the lookback window, skipping the most recent S days
        end_idx = -1 - S if S > 0 else -1
        start_idx = -(L + S)
        try:
            window_resid = resid.iloc[start_idx:end_idx].dropna()
        except Exception:
            continue
        if len(window_resid) < L * 0.5:  # tolerate some missing data
            continue
        scores[ticker] = float(window_resid.sum())

    return pd.Series(scores).sort_values(ascending=False)


def top_n_residual_momentum(prices: pd.DataFrame,
                             as_of: pd.Timestamp,
                             top_n: int = 3,
                             lookback_months: int = 12,
                             skip_months: int = 1,
                             regression_window_months: int = 36) -> list[str]:
    """Convenience: returns top-N tickers by residual momentum."""
    ff5 = get_ff5_aligned()
    scores = residual_momentum_score(
        prices, ff5, as_of, lookback_months, skip_months, regression_window_months
    )
    return scores.head(top_n).index.tolist()
