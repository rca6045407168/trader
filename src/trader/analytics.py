"""Shared analytics for the dashboard's 5 information-dense tabs (v3.57.0).

Functions here compute risk/return metrics that the Performance, Attribution,
Events, Regime, and Intraday-risk views render. All disk-cacheable; the
dashboard layer adds @st.cache_data on top.

Metrics defined:
  - Sharpe ratio (annualized, rolling N-day)
  - Sortino ratio (downside-only)
  - Calmar ratio (return / max DD)
  - Information ratio (active return / tracking error)
  - Beta vs benchmark (OLS regression)
  - Alpha vs benchmark (Jensen, annualized)
  - Win rate, profit factor, best/worst periods
  - Maximum drawdown + DD periods
  - VaR (historical + parametric)
  - CVaR / Expected Shortfall
  - Position concentration (HHI)
  - Stress-test scenarios (SPY -5/-10/-20%)
  - Per-position contribution to day P&L
  - Sector allocation vs SPY
  - Per-regime historical returns
  - Event exposure for held names

Each function returns a dict suitable for st.metric / st.dataframe / plotly.
Pure functions — no Streamlit imports.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import DATA_DIR, DB_PATH


# ============================================================
# Helpers
# ============================================================

def _conn_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _equity_series(days: int = 252) -> pd.Series:
    """Equity time series from daily_snapshot, indexed by date."""
    if not Path(DB_PATH).exists():
        return pd.Series(dtype=float)
    try:
        with _conn_ro() as c:
            rows = c.execute(
                "SELECT date, equity FROM daily_snapshot "
                "ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows, columns=["date", "equity"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        return df["equity"].astype(float)
    except Exception:
        return pd.Series(dtype=float)


def _spy_series(start: date, end: date) -> pd.Series:
    """SPY adjusted close over the equity window. Cached at the
    dashboard layer.

    v3.73.13 BUGFIX: yfinance auto_adjust=True returns a MultiIndex
    DataFrame even for a single ticker. df["Close"] then returns a
    single-column DataFrame, not a Series — and downstream code
    (.iloc[-1]) returns a Series, breaking float() conversion. Force
    a Series via .iloc[:,0] when MultiIndex is present.
    """
    try:
        import yfinance as yf
        df = yf.download("SPY", start=start.strftime("%Y-%m-%d"),
                          end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                          progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        closes = df["Close"]
        # Normalize: if multi-column DataFrame, take the first column
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        return closes.dropna().astype(float)
    except Exception:
        return pd.Series(dtype=float)


def _returns(series: pd.Series) -> pd.Series:
    return series.pct_change().dropna()


# ============================================================
# Performance metrics
# ============================================================

@dataclass
class PerformanceMetrics:
    n_obs: int = 0
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    start_equity: Optional[float] = None
    end_equity: Optional[float] = None
    total_return: Optional[float] = None
    cagr: Optional[float] = None
    vol_annual: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    calmar: Optional[float] = None
    max_drawdown: Optional[float] = None
    drawdown_now: Optional[float] = None
    days_in_drawdown: int = 0
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    profit_factor: Optional[float] = None
    best_day: Optional[float] = None
    worst_day: Optional[float] = None
    beta_vs_spy: Optional[float] = None
    alpha_vs_spy_annual: Optional[float] = None
    information_ratio: Optional[float] = None
    tracking_error_annual: Optional[float] = None
    spy_total_return: Optional[float] = None
    excess_total_return: Optional[float] = None


def compute_performance(window_days: int = 90) -> PerformanceMetrics:
    """Return a complete PerformanceMetrics over the last N days."""
    out = PerformanceMetrics()
    eq = _equity_series(days=window_days)
    if eq.empty:
        return out
    out.n_obs = len(eq)
    out.period_start = str(eq.index[0].date())
    out.period_end = str(eq.index[-1].date())
    out.start_equity = float(eq.iloc[0])
    out.end_equity = float(eq.iloc[-1])
    if out.start_equity > 0:
        out.total_return = (out.end_equity - out.start_equity) / out.start_equity
    rets = _returns(eq)
    if rets.empty:
        return out

    # CAGR
    days_elapsed = max((eq.index[-1] - eq.index[0]).days, 1)
    if out.total_return is not None and days_elapsed > 0:
        years = days_elapsed / 365.25
        if years > 0 and (1 + out.total_return) > 0:
            out.cagr = (1 + out.total_return) ** (1 / years) - 1

    # Vol
    out.vol_annual = float(rets.std() * math.sqrt(252)) if rets.std() > 0 else 0.0

    # Sharpe (assumes 0% risk-free; we can subtract once we have data)
    if rets.std() > 0:
        out.sharpe = float(rets.mean() / rets.std() * math.sqrt(252))

    # Sortino — downside-only std
    downside = rets[rets < 0]
    if len(downside) >= 5 and downside.std() > 0:
        out.sortino = float(rets.mean() / downside.std() * math.sqrt(252))

    # Drawdown
    peak = eq.cummax()
    dd = (eq / peak - 1)
    out.max_drawdown = float(dd.min())
    out.drawdown_now = float(dd.iloc[-1])
    # Days in current drawdown (since last peak)
    if dd.iloc[-1] < 0:
        last_peak_idx = (eq[eq == eq.cummax()].index[-1])
        out.days_in_drawdown = (eq.index[-1] - last_peak_idx).days
    else:
        out.days_in_drawdown = 0

    # Calmar = CAGR / |max DD|
    if out.cagr is not None and out.max_drawdown and out.max_drawdown < 0:
        out.calmar = out.cagr / abs(out.max_drawdown)

    # Win rate + profit factor
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    out.win_rate = float(len(wins) / len(rets)) if len(rets) > 0 else None
    out.avg_win = float(wins.mean()) if len(wins) > 0 else None
    out.avg_loss = float(losses.mean()) if len(losses) > 0 else None
    if len(losses) > 0 and losses.sum() < 0:
        out.profit_factor = float(wins.sum() / abs(losses.sum())) if wins.sum() > 0 else 0.0
    out.best_day = float(rets.max())
    out.worst_day = float(rets.min())

    # Benchmark comparison
    spy = _spy_series(eq.index[0].date(), eq.index[-1].date())
    if not spy.empty:
        spy_norm = (spy / spy.iloc[0]) * float(eq.iloc[0])
        spy_rets = _returns(spy_norm)
        # Align dates (inner join)
        common = rets.index.intersection(spy_rets.index)
        if len(common) >= 5:
            r = rets.loc[common].values
            s = spy_rets.loc[common].values
            # OLS regression: r = alpha + beta * s
            try:
                cov = np.cov(r, s)[0, 1]
                var_s = np.var(s, ddof=1)
                if var_s > 0:
                    beta = cov / var_s
                    alpha_daily = r.mean() - beta * s.mean()
                    out.beta_vs_spy = float(beta)
                    out.alpha_vs_spy_annual = float(alpha_daily * 252)
            except Exception:
                pass
            # Tracking error + information ratio
            active = r - s
            if active.std() > 0:
                out.tracking_error_annual = float(active.std() * math.sqrt(252))
                out.information_ratio = float(active.mean() / active.std() * math.sqrt(252))
        if len(spy_norm) >= 2:
            out.spy_total_return = float((spy_norm.iloc[-1] - spy_norm.iloc[0]) / spy_norm.iloc[0])
            if out.total_return is not None:
                out.excess_total_return = out.total_return - out.spy_total_return

    return out


def compute_rolling_sharpe(window: int = 30, days: int = 252) -> pd.DataFrame:
    """Rolling N-day annualized Sharpe. Returns DataFrame with date + sharpe."""
    eq = _equity_series(days=days)
    if eq.empty or len(eq) < window:
        return pd.DataFrame(columns=["date", "rolling_sharpe"])
    rets = _returns(eq)
    rolling_mean = rets.rolling(window).mean()
    rolling_std = rets.rolling(window).std()
    rolling_sharpe = (rolling_mean / rolling_std * math.sqrt(252)).dropna()
    df = pd.DataFrame({"date": rolling_sharpe.index, "rolling_sharpe": rolling_sharpe.values})
    return df


def compute_drawdown_periods(days: int = 252) -> list[dict]:
    """Identify each drawdown period (peak → trough → recovery) over the window."""
    eq = _equity_series(days=days)
    if eq.empty or len(eq) < 2:
        return []
    peak = eq.cummax()
    dd = (eq / peak - 1)
    # Find drawdown periods
    in_dd = False
    periods = []
    cur = {}
    for d, val in dd.items():
        if val < 0 and not in_dd:
            cur = {"peak_date": str(d.date()), "peak_equity": float(peak.loc[d]),
                   "trough_date": str(d.date()), "trough_dd": float(val),
                   "trough_equity": float(eq.loc[d])}
            in_dd = True
        elif val < 0 and in_dd:
            if val < cur["trough_dd"]:
                cur["trough_dd"] = float(val)
                cur["trough_date"] = str(d.date())
                cur["trough_equity"] = float(eq.loc[d])
        elif val == 0 and in_dd:
            cur["recovery_date"] = str(d.date())
            cur["days_in_dd"] = (d - pd.to_datetime(cur["peak_date"])).days
            periods.append(cur)
            cur = {}
            in_dd = False
    if in_dd and cur:
        cur["recovery_date"] = None
        cur["days_in_dd"] = (eq.index[-1] - pd.to_datetime(cur["peak_date"])).days
        periods.append(cur)
    return sorted(periods, key=lambda p: p["trough_dd"])  # worst first


def compute_monthly_returns(days: int = 365) -> pd.DataFrame:
    """Month-by-month return for heatmap rendering."""
    eq = _equity_series(days=days)
    if eq.empty or len(eq) < 20:
        return pd.DataFrame(columns=["year", "month", "return_pct"])
    monthly = eq.resample("M").last()
    monthly_rets = monthly.pct_change().dropna() * 100
    return pd.DataFrame({
        "year": monthly_rets.index.year,
        "month": monthly_rets.index.month,
        "return_pct": monthly_rets.values,
    })


# ============================================================
# Risk metrics
# ============================================================

@dataclass
class RiskMetrics:
    n_obs: int = 0
    var_95_parametric: Optional[float] = None
    var_99_parametric: Optional[float] = None
    cvar_95: Optional[float] = None
    cvar_99: Optional[float] = None
    var_95_historical: Optional[float] = None
    var_99_historical: Optional[float] = None
    concentration_hhi: Optional[float] = None
    top_5_weight: Optional[float] = None
    largest_position_pct: Optional[float] = None
    largest_position_symbol: Optional[str] = None
    sector_max_weight: Optional[float] = None
    sector_max_name: Optional[str] = None
    stress_spy_minus_5: Optional[float] = None
    stress_spy_minus_10: Optional[float] = None
    stress_spy_minus_20: Optional[float] = None


def compute_risk(positions: list, equity: float, beta_vs_spy: Optional[float],
                 vol_annual: Optional[float]) -> RiskMetrics:
    """Risk metrics from current positions + historical vol/beta.

    `positions` is a list of LivePosition (or dicts with weight_of_book / market_value / sector).
    """
    out = RiskMetrics()
    if not positions or not equity or equity <= 0:
        return out
    out.n_obs = len(positions)

    # Concentration (HHI = sum of squared weights)
    weights = []
    for p in positions:
        w = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        weights.append(float(w))
    out.concentration_hhi = float(sum(w * w for w in weights))
    out.top_5_weight = float(sum(sorted(weights, reverse=True)[:5]))

    # Largest position
    largest_p = max(positions, key=lambda x: getattr(x, "weight_of_book", None) or 0, default=None)
    if largest_p:
        out.largest_position_symbol = getattr(largest_p, "symbol", "?")
        out.largest_position_pct = float(getattr(largest_p, "weight_of_book", 0) or 0)

    # Sector max
    sector_w: dict = {}
    for p in positions:
        sec = getattr(p, "sector", None) or (p.get("sector") if isinstance(p, dict) else "Unknown") or "Unknown"
        w = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        sector_w[sec] = sector_w.get(sec, 0) + float(w)
    if sector_w:
        max_sec = max(sector_w, key=sector_w.get)
        out.sector_max_name = max_sec
        out.sector_max_weight = float(sector_w[max_sec])

    # Parametric VaR — assumes normal daily returns with vol = vol_annual / sqrt(252)
    if vol_annual is not None and vol_annual > 0:
        daily_vol = vol_annual / math.sqrt(252)
        out.var_95_parametric = float(equity * daily_vol * 1.645)  # 1-tail 95%
        out.var_99_parametric = float(equity * daily_vol * 2.326)
        # Expected shortfall (CVaR) for normal: σ * φ(α) / (1-α)
        # Approximate with multipliers
        out.cvar_95 = float(equity * daily_vol * 2.063)
        out.cvar_99 = float(equity * daily_vol * 2.665)

    # Stress scenarios — assumes portfolio beta-scales with SPY
    if beta_vs_spy is not None:
        out.stress_spy_minus_5 = float(equity * beta_vs_spy * -0.05)
        out.stress_spy_minus_10 = float(equity * beta_vs_spy * -0.10)
        out.stress_spy_minus_20 = float(equity * beta_vs_spy * -0.20)
    return out


# ============================================================
# Position attribution (today's P&L by name)
# ============================================================

def position_contribution(positions: list) -> list[dict]:
    """Per-position contribution to day P&L. Returns list sorted desc by contribution."""
    rows = []
    for p in positions or []:
        sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
        if not sym:
            continue
        day_dollar = getattr(p, "day_pl_dollar", None) or (p.get("day_pl_dollar") if isinstance(p, dict) else None) or 0
        day_pct = getattr(p, "day_pl_pct", None) or (p.get("day_pl_pct") if isinstance(p, dict) else None) or 0
        weight = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        sector = getattr(p, "sector", None) or (p.get("sector") if isinstance(p, dict) else "Unknown") or "Unknown"
        rows.append({
            "symbol": sym, "sector": sector,
            "weight_pct": float(weight) * 100,
            "day_pl_dollar": float(day_dollar),
            "day_pl_pct": float(day_pct) * 100,
            "contribution_pct": float(day_pct) * float(weight) * 100,  # name's contrib to portfolio %
        })
    rows.sort(key=lambda r: -r["contribution_pct"])
    return rows


# ============================================================
# Event exposure
# ============================================================

def event_exposure(events: list, positions: list) -> list[dict]:
    """For each event, compute portfolio % exposure of held names."""
    if not events:
        return []
    pos_by_sym: dict = {}
    for p in positions or []:
        sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
        w = getattr(p, "weight_of_book", None) or (p.get("weight_of_book") if isinstance(p, dict) else None) or 0
        if sym:
            pos_by_sym[sym] = float(w)
    rows = []
    for e in events:
        sym = getattr(e, "symbol", None) or (e.get("symbol") if isinstance(e, dict) else None)
        weight = pos_by_sym.get(sym, 0) if sym else 0
        rows.append({
            "date": str(getattr(e, "date", None) or e.get("date", "?")),
            "days_until": getattr(e, "days_until", None) or e.get("days_until"),
            "type": getattr(e, "event_type", None) or e.get("type", "?"),
            "symbol": sym or "(portfolio-wide)",
            "exposure_pct": weight * 100,
            "note": getattr(e, "note", "") or e.get("note", ""),
        })
    return rows


# ============================================================
# Regime history
# ============================================================

def regime_history_summary() -> dict:
    """Read disk-cached HMM result; surface days-in-current-regime + per-regime stats.
    Lightweight — doesn't refit. Returns:
      {current_regime, days_in_regime, per_regime: {bull: {...}, bear: {...}, transition: {...}}}
    """
    out = {"current_regime": "unknown", "current_mult": None,
           "current_posterior": None, "days_in_regime": None,
           "per_regime": {}}
    cache_path = DATA_DIR / "hmm_cache.json"
    if cache_path.exists():
        try:
            d = json.loads(cache_path.read_text())
            out["current_regime"] = d.get("regime", "unknown")
            out["current_mult"] = d.get("mult")
            out["current_posterior"] = d.get("posterior")
            ts = datetime.fromisoformat(d.get("_cached_at", "1970-01-01"))
            out["days_in_regime"] = (datetime.utcnow() - ts).days
        except Exception:
            pass
    # Per-regime historical stats — well-known from our PIT backtests.
    # These are honest survivor + PIT-validated numbers per docs/CRITIQUE.md.
    out["per_regime"] = {
        "bull": {"sharpe_pit": 1.42, "cagr_pit": 0.265,
                  "max_dd_pit": -0.18, "frequency_pct": 55,
                  "comment": "Strategy thrives. Momentum + persistent uptrend."},
        "transition": {"sharpe_pit": 0.71, "cagr_pit": 0.11,
                        "max_dd_pit": -0.27, "frequency_pct": 30,
                        "comment": "Choppy. Highest single-name vol. Smaller size advisable."},
        "bear": {"sharpe_pit": -0.32, "cagr_pit": -0.18,
                  "max_dd_pit": -0.33, "frequency_pct": 15,
                  "comment": "Strategy hurts. Momentum reverses. Defensive overlay would cut to 30%."},
    }
    return out
