"""[v3.59.1 — V5 Sleeve C SCAFFOLD] ML-augmented PEAD sleeve.

Post-Earnings-Announcement Drift (Bernard-Thomas 1989), but ranked using
features that include the *history* of prior earnings surprises for the
same name — not just the latest one. Per ScienceDirect 2024 "Beyond the
last surprise: Reviving PEAD with machine learning and historical
earnings," this formulation roughly doubles the Sharpe vs. naive PEAD.

⚠️  This is a SCAFFOLD. Status defaults to NOT_WIRED. Promotion requires:
   1. Build feature pipeline on >=8 quarters of surprise history per name
   2. Train rolling-window gradient-boosted ranker (lightgbm)
   3. Leakage audit (no post-release data in features)
   4. 3-gate validation
   5. 30-day shadow validation

Free-tier data adapter:
  • yfinance Ticker.earnings_history gives EPS estimate vs actual for
    last ~4 quarters. Free but limited backfill.
  • For richer feature engineering, the proposal originally specified
    Finnhub paid tier ($50/mo for 5+ years). Per "added free system"
    instruction, this scaffold uses ONLY yfinance free tier; longer
    surprise-history features are gated until/unless a paid feed is wired.

Module exposes:
  • compute_features(ticker) → SUE sequence + run-length + decay slope
  • rank_today(universe) → cross-sectional rank score per name
  • expected_targets(universe, n_holdings) → {sym: weight} dict
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


SLEEVE_ALLOCATION_PCT_DEFAULT = 0.10
N_HOLDINGS = 5  # 5-8 names per V5 proposal


@dataclass
class PeadFeatures:
    ticker: str
    last_surprise_pct: Optional[float] = None
    sue_sequence: list[float] = field(default_factory=list)  # standardized unexpected earnings
    surprise_sign_run_length: int = 0  # consecutive same-sign surprises
    decay_slope: Optional[float] = None  # slope of |surprise| over time
    last_earnings_date: Optional[date] = None
    days_since_earnings: Optional[int] = None
    error: Optional[str] = None


def status() -> str:
    return os.getenv("PEAD_SLEEVE_STATUS", "NOT_WIRED").upper()


def sleeve_capital_pct() -> float:
    try:
        return float(os.getenv("PEAD_SLEEVE_PCT",
                                str(SLEEVE_ALLOCATION_PCT_DEFAULT)))
    except Exception:
        return SLEEVE_ALLOCATION_PCT_DEFAULT


def _surprise_sign(s: float) -> int:
    if s > 0: return 1
    if s < 0: return -1
    return 0


def _run_length(signs: list[int]) -> int:
    """Length of the streak ending at the most recent value."""
    if not signs:
        return 0
    last = signs[-1]
    if last == 0:
        return 0
    n = 0
    for s in reversed(signs):
        if s == last:
            n += 1
        else:
            break
    return n


def compute_features(ticker: str) -> PeadFeatures:
    """yfinance-based free-tier feature extraction. Returns empty
    features (with .error set) if data unavailable."""
    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(ticker)
        # earnings_dates: DataFrame indexed by date with EPS_estimate, Reported_EPS, Surprise_pct
        df = getattr(t, "earnings_dates", None)
        if df is None or (hasattr(df, "empty") and df.empty):
            return PeadFeatures(ticker=ticker,
                                  error="no earnings_dates from yfinance")
        df = df.dropna(subset=["Surprise(%)"]) if "Surprise(%)" in df.columns else df
        if df.empty:
            return PeadFeatures(ticker=ticker,
                                  error="all surprise rows are NaN")
        df = df.sort_index()  # oldest → newest
        surprises = df["Surprise(%)"].astype(float).tolist()
        signs = [_surprise_sign(s) for s in surprises]
        last = surprises[-1] if surprises else None
        sue = surprises[-8:]  # last 8 quarters
        # Decay slope: linear regression of |surprise| over index
        slope = None
        if len(sue) >= 4:
            xs = list(range(len(sue)))
            ys = [abs(v) for v in sue]
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys) / len(ys)
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            den = sum((x - mean_x) ** 2 for x in xs) or 1
            slope = num / den
        last_date = None
        days_since = None
        try:
            last_idx = df.index[-1]
            if hasattr(last_idx, "date"):
                last_date = last_idx.date()
                days_since = (datetime.utcnow().date() - last_date).days
        except Exception:
            pass
        return PeadFeatures(
            ticker=ticker, last_surprise_pct=last,
            sue_sequence=sue,
            surprise_sign_run_length=_run_length(signs),
            decay_slope=slope,
            last_earnings_date=last_date,
            days_since_earnings=days_since,
        )
    except Exception as e:
        return PeadFeatures(ticker=ticker,
                              error=f"{type(e).__name__}: {e}")


def rank_today(universe: list[str], window_days: int = 60) -> list[tuple[str, float]]:
    """Cross-sectional rank: for each name with earnings in the last
    `window_days`, compute a composite score from the features.
    Returns [(ticker, score), ...] sorted descending. Scaffold formula
    (NOT a trained model — explicit placeholder):
      score = last_surprise * (1 + run_length/4) * exp(-days_since/30)
    Trained-model replacement is a follow-up commit gated on real
    backtest validation."""
    out: list[tuple[str, float]] = []
    for sym in universe:
        f = compute_features(sym)
        if f.error or f.last_surprise_pct is None:
            continue
        if f.days_since_earnings is None or f.days_since_earnings > window_days:
            continue
        if f.days_since_earnings < 0:
            continue
        # Composite score (scaffold; not trained)
        run_boost = 1 + min(f.surprise_sign_run_length, 4) / 4
        time_decay = math.exp(-f.days_since_earnings / 30)
        score = f.last_surprise_pct * run_boost * time_decay
        out.append((sym, score))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def expected_targets(universe: list[str],
                       n_holdings: int = N_HOLDINGS) -> dict[str, float]:
    """Return {symbol: weight} for the top-n_holdings names in the
    sleeve. Equal-weighted. Empty dict if no name in window or if
    sleeve is NOT_WIRED."""
    if status() == "NOT_WIRED":
        return {}
    ranked = rank_today(universe)
    if not ranked:
        return {}
    picks = ranked[:n_holdings]
    # Only take positive scores (positive surprise drift)
    picks = [p for p in picks if p[1] > 0]
    if not picks:
        return {}
    total_pct = sleeve_capital_pct()
    per = total_pct / len(picks)
    return {sym: per for sym, _ in picks}
