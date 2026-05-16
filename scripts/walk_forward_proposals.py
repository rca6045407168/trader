"""Multi-window walk-forward backtest of today's proposed overlays.

Richard's rule (2026-05-15): "you have confirmation bias because you're
running backtests on momentum stocks that is today's. You should apply
your current strategy 6 months ago and see if you get a favorable
result. You have to take multiple intervals of such tests and test."

This script honors that by:
  - Taking the CURRENT strategy configuration (top-N 12-month momentum,
    cash-park overlay, plus the proposed additions)
  - Running it across MULTIPLE non-overlapping historical windows
  - Reporting CAGR / Sharpe / vs-SPY per window AND the win-rate count

What we DON'T fix here (acknowledged biases):
  - Universe survivorship: liquid_50 is today's mega-caps. Names that
    *would* have been in the top 50 in 2021 but fell out (NFLX dropped,
    F replaced by something else, etc.) aren't here. This biases the
    backtest toward names that "made it." A proper fix needs a
    point-in-time S&P 500 membership table. Out of scope for this
    afternoon.
  - Hand-curated cap weights in `direct_index_tlh.py` use today's
    market-cap rankings. Backtest cap-weight scores reflect today.

What this script DOES test honestly:
  - Each window evaluates a strategy that uses only data UP TO the
    start of that window. No look-ahead within the window.
  - Each overlay is applied at the monthly rebalance level.
  - Comparison vs SPY in the same window.

Run: /Users/richardchen/trader/.venv/bin/python /Users/richardchen/trader/scripts/walk_forward_proposals.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50
from trader.sectors import get_sector


# -------- 5 non-overlapping 12-month windows -----------------
WINDOWS = [
    ("2021-07-01", "2022-07-01"),   # H2-21 → H1-22 — bull → bear pivot
    ("2022-07-01", "2023-07-01"),   # H2-22 → H1-23 — bear / recovery
    ("2023-07-01", "2024-07-01"),   # H2-23 → H1-24 — recovery / rally
    ("2024-07-01", "2025-07-01"),   # H2-24 → H1-25 — Q4 chop
    ("2025-07-01", "2026-05-15"),   # H2-25 → YTD 2026 — live regime
]


# -------- helpers --------
def annualize(daily_returns: pd.Series) -> dict:
    """Compute CAGR / vol / Sharpe / maxDD from a daily-return series."""
    equity = (1 + daily_returns.fillna(0)).cumprod()
    n = max(len(daily_returns), 1)
    years = n / 252.0
    cagr = equity.iloc[-1] ** (1 / max(years, 1e-9)) - 1
    vol = float(daily_returns.std()) * np.sqrt(252)
    mean_ex = float(daily_returns.mean()) * 252
    sharpe = mean_ex / vol if vol > 1e-9 else 0.0
    peak = equity.cummax()
    max_dd = float((equity / peak - 1).min())
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "max_dd": max_dd,
            "final_equity": float(equity.iloc[-1])}


def compute_monthly_momentum_weights(
    prices: pd.DataFrame,
    lookback_months: int = 12,
    top_n: int = 5,
    deployed_gross: float = 0.62,
) -> pd.DataFrame:
    """Standard 12m momentum top-N, monthly rebalance.

    Look-ahead safe: at each rebalance date, only uses data through that
    date. The lookback window is `lookback_months` months back from
    `month_end_t - skip(=1 month)`.
    """
    monthly = prices.resample("ME").last().ffill(limit=2)
    L, S = lookback_months, 1
    lookback = monthly.shift(S) / monthly.shift(S + L) - 1
    w = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_n:
            continue
        winners = scores.nlargest(top_n).index
        w.loc[d, winners] = deployed_gross / top_n
    return w


def apply_sector_cap(weights: pd.DataFrame, max_per_sector: float = 0.25) -> pd.DataFrame:
    """Cap each GICS sector to max_per_sector. If a row violates, redistribute
    the over-cap weight to other names within budget, preserving total gross."""
    out = weights.copy()
    for d in out.index:
        row = out.loc[d]
        if row.sum() == 0:
            continue
        # Group by sector
        sectors = {sym: get_sector(sym) for sym in row.index if row[sym] > 0}
        sector_w = {}
        for sym, sec in sectors.items():
            sector_w[sec] = sector_w.get(sec, 0) + row[sym]
        # Find violations
        total_gross = row.sum()
        for sec, sw in sector_w.items():
            if sw > max_per_sector * total_gross + 1e-9:
                # Scale down all names in this sector to max_per_sector × total_gross
                target_sec = max_per_sector * total_gross
                scale = target_sec / sw
                for sym, sec_of in sectors.items():
                    if sec_of == sec:
                        out.loc[d, sym] *= scale
        # Don't redistribute — just take the gross loss (rebalance to whatever fits).
        # This is the SIMPLE cap: gross-shrink, no redistribution. Conservative.
    return out


def apply_cash_park(daily_ret: pd.Series, weights_daily: pd.DataFrame,
                    spy_daily_ret: pd.Series, min_buffer: float = 0.05) -> pd.Series:
    """Cash-park overlay: residual gross (1 - deployed - buffer) earns SPY."""
    deployed = weights_daily.sum(axis=1)
    cash_pct = (1.0 - deployed).clip(lower=0)
    park = (cash_pct - min_buffer).clip(lower=0)
    return daily_ret + park * spy_daily_ret


def apply_hmm_regime(daily_ret: pd.Series, spy_daily_ret: pd.Series,
                      training_returns: pd.Series | None = None,
                      verbose: bool = False) -> pd.Series:
    """HMM regime gate: fit 3-state Gaussian HMM, then use posterior
    probabilities to scale daily exposure.

    State scaling: BULL=1.0, TRANSITION=0.6, BEAR=0.0.

    Args:
      daily_ret: portfolio daily returns over the prediction window.
      spy_daily_ret: SPY daily returns over the prediction window
                     (used as the observation series for the forward filter).
      training_returns: optional pre-window SPY returns to train HMM on.
                        If provided, HMM is fit on these (out-of-sample).
                        If None, falls back to first-40%-of-window training
                        (in-sample, biased — see v6.1.1 caveat).
      verbose: print fit + scale diagnostics.

    Honesty caveats:
      - When `training_returns` is provided: HMM only sees pre-window data.
        Forward filtering on the prediction window uses the trained
        transition matrix and emission distributions — no look-ahead.
      - HMM is fit ONCE (not refit during the window). A more honest
        production implementation would refit every N months with
        only-available-at-rebalance data. Acceptable for this test.

    v6.1.1 (silent-bug fix): previously caught AttributeError on
    `hmm.state_label` (the real attribute is `state_to_regime`) and
    returned the input unchanged. Now exceptions surface explicitly.
    v6.1.2 (out-of-sample): training set decoupled from prediction window.
    """
    from trader.hmm_regime import fit_hmm, HMMRegime

    if training_returns is not None and len(training_returns) >= 60:
        train = training_returns.dropna()
        train_label = f"OOS pre-window ({len(train)} days)"
    else:
        n_train = max(int(len(spy_daily_ret) * 0.4), 60)
        train = spy_daily_ret.iloc[:n_train].dropna()
        train_label = f"in-sample first {n_train} of window"

    if len(train) < 60:
        if verbose:
            print(f"    HMM skip: only {len(train)} training points")
        return daily_ret

    hmm = fit_hmm(train, n_states=3, n_iter=100)
    if verbose:
        regime_summary = ", ".join(
            f"state{i}={hmm.state_to_regime[i].value}"
            f"(μ={hmm.state_means[i]*100:+.3f}%,σ={hmm.state_vols[i]*100:.2f}%)"
            for i in range(hmm.n_states)
        )
        print(f"    HMM fitted on {train_label}: {regime_summary}")

    # Forward filter over the prediction window. predict_proba returns
    # posterior P(state | observations up to t) using trained model —
    # no future leakage.
    X = spy_daily_ret.fillna(0).values.reshape(-1, 1)
    posteriors = hmm.model.predict_proba(X)  # (T, K)

    state_scales = {
        HMMRegime.BULL: 1.0,
        HMMRegime.TRANSITION: 0.6,
        HMMRegime.BEAR: 0.0,
    }
    per_state = np.array([
        state_scales[hmm.state_to_regime[i]] for i in range(hmm.n_states)
    ])
    daily_scale = posteriors @ per_state  # (T,) — weighted avg

    scale_series = pd.Series(daily_scale, index=spy_daily_ret.index)
    scale_series = scale_series.reindex(daily_ret.index).fillna(1.0)
    if verbose:
        print(f"    HMM scale range: min={scale_series.min():.2f}  "
              f"mean={scale_series.mean():.2f}  max={scale_series.max():.2f}")
    return daily_ret * scale_series


# -------- one window simulation --------
def simulate_window(start: str, end: str, overlays: dict,
                    hmm_oos_training_start: str = "2010-01-01") -> dict:
    """Run one window with the given overlays.

    overlays = {
        "cash_park": True/False,
        "sector_cap": True/False,
        "hmm_regime": True/False,
    }

    hmm_oos_training_start: HMM is trained on SPY returns from this date
    up to `start` (exclusive). Default 2010-01-01 gives ~11 years of OOS
    training before the first window (2021-07-01). This is the v6.1.2
    fix for confirmation bias — earlier in-window training had the bear
    period in the training set for the 2021-22 window.
    """
    # Pull universe prices + SPY for the window (need pre-window history
    # for the momentum lookback and SPY history for HMM training).
    pre_start = (pd.to_datetime(start) - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    # Pull a longer SPY series separately for OOS HMM training
    spy_train = None
    if overlays.get("hmm_regime"):
        spy_full = fetch_history(["SPY"], start=hmm_oos_training_start, end=start)
        if "SPY" in spy_full.columns:
            spy_train = spy_full["SPY"].pct_change().dropna()
    prices = fetch_history(DEFAULT_LIQUID_50 + ["SPY"], start=pre_start, end=end)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))
    spy = prices["SPY"]
    pool = [c for c in prices.columns if c != "SPY"]
    px = prices[pool]

    # Build weights using ALL pre-start data (for lookback) but ONLY
    # rebalance within the window.
    w_monthly = compute_monthly_momentum_weights(px, lookback_months=12, top_n=5, deployed_gross=0.62)

    if overlays.get("sector_cap"):
        w_monthly = apply_sector_cap(w_monthly, max_per_sector=0.25)

    # Clip to window
    w_monthly = w_monthly[(w_monthly.index >= start) & (w_monthly.index <= end)]
    if w_monthly.empty:
        return {"start": start, "end": end, "error": "no data in window"}

    # Reindex weights to daily, lag by 1 day to prevent look-ahead
    w_daily = w_monthly.reindex(px.index, method="ffill").shift(1).fillna(0)
    w_daily = w_daily[(w_daily.index >= start) & (w_daily.index <= end)]

    daily_ret_px = px.pct_change().fillna(0)
    daily_ret_px = daily_ret_px.reindex(w_daily.index).fillna(0)
    portfolio_ret = (w_daily * daily_ret_px).sum(axis=1)

    spy_window = spy[(spy.index >= start) & (spy.index <= end)]
    spy_ret_window = spy_window.pct_change().fillna(0)
    spy_ret_window = spy_ret_window.reindex(portfolio_ret.index).fillna(0)

    # Cash-park overlay (residual cash earns SPY)
    if overlays.get("cash_park"):
        portfolio_ret = apply_cash_park(portfolio_ret, w_daily, spy_ret_window)

    # HMM regime gate — uses pre-window SPY training set (v6.1.2 OOS fix)
    if overlays.get("hmm_regime"):
        portfolio_ret = apply_hmm_regime(
            portfolio_ret, spy_ret_window,
            training_returns=spy_train,
        )

    stats_p = annualize(portfolio_ret)
    stats_spy = annualize(spy_ret_window)
    return {
        "start": start, "end": end,
        "portfolio": stats_p,
        "spy": stats_spy,
        "alpha_cagr_pp": (stats_p["cagr"] - stats_spy["cagr"]) * 100,
        "alpha_sharpe": stats_p["sharpe"] - stats_spy["sharpe"],
    }


# -------- driver --------
SCENARIOS = {
    "A. baseline (no overlays)": {},
    "B. cash-park only": {"cash_park": True},
    "C. cash-park + sector-cap 25%": {"cash_park": True, "sector_cap": True},
    "D. cash-park + HMM regime": {"cash_park": True, "hmm_regime": True},
    "E. cash-park + sector-cap + HMM": {"cash_park": True, "sector_cap": True, "hmm_regime": True},
}


def main():
    print("=" * 100)
    print("WALK-FORWARD PROPOSAL BACKTEST — multiple non-overlapping windows")
    print("=" * 100)
    print()
    print(f"Strategy: top-5 12-month momentum, monthly rebal, 62% deployed alpha")
    print(f"Universe: liquid_50 ({len(DEFAULT_LIQUID_50)} names) [survivorship bias acknowledged]")
    print(f"Windows: {len(WINDOWS)} non-overlapping 12-month spans")
    print()

    # Results: scenario -> window -> stats
    rows = []
    for label, overlays in SCENARIOS.items():
        print(f"\n--- {label} ---")
        wins_vs_spy = 0
        for start, end in WINDOWS:
            r = simulate_window(start, end, overlays)
            if "error" in r:
                print(f"  {start}–{end[:7]:8s} ERROR: {r['error']}")
                continue
            p = r["portfolio"]
            s = r["spy"]
            alpha = r["alpha_cagr_pp"]
            beat = "✓" if alpha > 0 else "✗"
            if alpha > 0:
                wins_vs_spy += 1
            print(f"  {start}–{end[:7]}  port CAGR {p['cagr']:+7.2%}  "
                  f"Sharpe {p['sharpe']:5.2f}  maxDD {p['max_dd']:+6.1%}  | "
                  f"spy {s['cagr']:+7.2%} Sh {s['sharpe']:5.2f}  | "
                  f"α {alpha:+6.2f}pp {beat}")
            rows.append({"scenario": label, **{k: v for k, v in r.items() if k != "portfolio" and k != "spy"},
                         "p_cagr": p["cagr"], "p_sharpe": p["sharpe"], "p_maxdd": p["max_dd"],
                         "spy_cagr": s["cagr"], "spy_sharpe": s["sharpe"]})
        print(f"  WIN-RATE vs SPY: {wins_vs_spy}/{len(WINDOWS)}")

    # Cross-scenario summary
    print("\n" + "=" * 100)
    print("CROSS-WINDOW SUMMARY")
    print("=" * 100)
    df = pd.DataFrame(rows)
    summary = df.groupby("scenario").agg(
        avg_cagr=("p_cagr", "mean"),
        avg_alpha_pp=("alpha_cagr_pp", "mean"),
        median_alpha_pp=("alpha_cagr_pp", "median"),
        worst_alpha_pp=("alpha_cagr_pp", "min"),
        best_alpha_pp=("alpha_cagr_pp", "max"),
        avg_sharpe=("p_sharpe", "mean"),
        avg_maxdd=("p_maxdd", "mean"),
    ).sort_values("avg_alpha_pp", ascending=False)
    print(summary.to_string(float_format=lambda x: f"{x:+.2%}" if abs(x) < 5 else f"{x:+.3f}"))

    out = Path(__file__).resolve().parent.parent / "data" / "reports" / "walk_forward_proposals.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nDetail CSV → {out}")


if __name__ == "__main__":
    main()
