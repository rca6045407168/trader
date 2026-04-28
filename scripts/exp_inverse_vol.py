"""Empirical comparison: equal-weight vs inverse-vol weight on top-5 momentum.

Universe: liquid_50. Window: 2015-2025. Daily prices, 60-day daily-vol weights.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

from trader.data import fetch_history
from trader.universe import DEFAULT_LIQUID_50


def _stats(equity: pd.Series, monthly_ret: pd.Series) -> dict:
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    ann_vol = monthly_ret.std() * math.sqrt(12)
    sharpe = (monthly_ret.mean() * 12) / ann_vol if ann_vol > 0 else 0.0
    running_max = equity.cummax()
    max_dd = (equity / running_max - 1).min()
    return {
        "cagr": float(cagr),
        "sharpe": float(sharpe),
        "ann_vol": float(ann_vol),
        "max_dd": float(max_dd),
    }


def run(
    universe: list[str],
    start: str = "2015-01-01",
    end: str = "2025-01-01",
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    vol_window: int = 60,
    slippage_bps: float = 5.0,
):
    prices = fetch_history(universe, start=start, end=end)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))

    daily_ret = prices.pct_change()
    # 60-day rolling daily vol
    rolling_vol = daily_ret.rolling(vol_window).std()

    monthly = prices.resample("ME").last().ffill(limit=2)
    monthly_ret = monthly.pct_change()
    # Align rolling vol to month-end
    monthly_vol = rolling_vol.resample("ME").last().reindex(monthly.index)

    L, S = lookback_months, skip_months
    lookback = monthly.shift(S) / monthly.shift(S + L) - 1

    weights_eq = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    weights_iv = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)

    momentum_log = []  # collect winners + their vols + their assigned weights for diagnostics

    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_n:
            continue
        winners = scores.nlargest(top_n).index
        # Equal weight
        weights_eq.loc[d, winners] = 1.0 / top_n
        # Inverse-vol weight using 60d daily vol AS OF date d
        vols = monthly_vol.loc[d, winners]
        if vols.isna().any() or (vols <= 0).any():
            # fallback: equal weight if any vol missing/zero
            weights_iv.loc[d, winners] = 1.0 / top_n
            momentum_log.append({"date": d, "fallback": True})
            continue
        inv = 1.0 / vols
        w = inv / inv.sum()
        weights_iv.loc[d, winners] = w.values
        momentum_log.append({
            "date": d,
            "winners": list(winners),
            "vols": vols.to_dict(),
            "mom_scores": scores.loc[winners].to_dict(),
            "iv_weights": w.to_dict(),
            "fallback": False,
        })

    def _portfolio_returns(weights: pd.DataFrame) -> pd.Series:
        turnover = weights.diff().abs().sum(axis=1).fillna(0)
        slippage = turnover * (slippage_bps / 10_000)
        gross = (weights.shift(1) * monthly_ret).sum(axis=1)
        return (gross - slippage).fillna(0)

    ret_eq = _portfolio_returns(weights_eq)
    ret_iv = _portfolio_returns(weights_iv)

    eq_eq = (1 + ret_eq).cumprod()
    eq_iv = (1 + ret_iv).cumprod()

    s_eq = _stats(eq_eq, ret_eq)
    s_iv = _stats(eq_iv, ret_iv)

    return {
        "stats_eq": s_eq,
        "stats_iv": s_iv,
        "ret_eq": ret_eq,
        "ret_iv": ret_iv,
        "weights_eq": weights_eq,
        "weights_iv": weights_iv,
        "momentum_log": momentum_log,
    }


def main():
    res = run(DEFAULT_LIQUID_50, start="2015-01-01", end="2025-01-01")
    s_eq, s_iv = res["stats_eq"], res["stats_iv"]

    print("=" * 72)
    print("VARIANT A: Equal-weight top-5 momentum")
    print(f"  CAGR        : {s_eq['cagr']*100:7.2f}%")
    print(f"  Sharpe      : {s_eq['sharpe']:7.3f}")
    print(f"  Ann Vol     : {s_eq['ann_vol']*100:7.2f}%")
    print(f"  Max DD      : {s_eq['max_dd']*100:7.2f}%")
    print()
    print("VARIANT B: Inverse-vol weight top-5 momentum (60d daily vol)")
    print(f"  CAGR        : {s_iv['cagr']*100:7.2f}%")
    print(f"  Sharpe      : {s_iv['sharpe']:7.3f}")
    print(f"  Ann Vol     : {s_iv['ann_vol']*100:7.2f}%")
    print(f"  Max DD      : {s_iv['max_dd']*100:7.2f}%")
    print()
    print("DELTA (B - A)")
    print(f"  CAGR        : {(s_iv['cagr']-s_eq['cagr'])*100:+7.2f} pp")
    print(f"  Sharpe      : {s_iv['sharpe']-s_eq['sharpe']:+7.3f}")
    print(f"  Max DD      : {(s_iv['max_dd']-s_eq['max_dd'])*100:+7.2f} pp")
    print("=" * 72)

    # ---- Failure-mode diagnostics ----
    log = [r for r in res["momentum_log"] if not r.get("fallback") and "vols" in r]

    # 1) Did inverse-vol underweight the highest-momentum names?
    # For each rebal: rank winners by momentum (1=highest) and by IV weight (1=largest weight)
    # If IV consistently puts highest-momentum into LOW weight rank, that's underweighting.
    rank_pairs = []
    for r in log:
        winners = list(r["winners"])
        moms = pd.Series(r["mom_scores"])
        wts = pd.Series(r["iv_weights"])
        mom_rank = moms.rank(ascending=False)  # 1 = best momentum
        wt_rank = wts.rank(ascending=False)    # 1 = largest weight
        for t in winners:
            rank_pairs.append((mom_rank[t], wt_rank[t]))
    rp = pd.DataFrame(rank_pairs, columns=["mom_rank", "wt_rank"])
    corr_rank = rp.corr().iloc[0, 1]
    avg_wt_top_mom = rp[rp["mom_rank"] == 1.0]["wt_rank"].mean()
    avg_wt_low_mom = rp[rp["mom_rank"] == 5.0]["wt_rank"].mean()
    print()
    print("FAILURE-MODE 1: Did inverse-vol underweight top-momentum names?")
    print(f"  Spearman-ish rank corr(mom_rank, weight_rank) over all picks: {corr_rank:+.3f}")
    print(f"  (>0 means top-momentum -> low weight = UNDERWEIGHTING)")
    print(f"  Avg weight-rank for #1 momentum pick : {avg_wt_top_mom:.2f}  (1=heaviest, 5=lightest)")
    print(f"  Avg weight-rank for #5 momentum pick : {avg_wt_low_mom:.2f}")

    # 2) Sharpe lift consistency by year
    diff = res["ret_iv"] - res["ret_eq"]
    by_year = diff.groupby(diff.index.year).agg(["mean", "std", "count"])
    by_year["t_stat"] = by_year["mean"] / (by_year["std"] / np.sqrt(by_year["count"])).replace(0, np.nan)
    print()
    print("FAILURE-MODE 2: Yearly Sharpe-diff (IV - EQ) consistency")
    yearly_eq = res["ret_eq"].groupby(res["ret_eq"].index.year)
    yearly_iv = res["ret_iv"].groupby(res["ret_iv"].index.year)

    def _ann_sharpe(s):
        if s.std() == 0:
            return 0.0
        return s.mean() * 12 / (s.std() * math.sqrt(12))

    rows = []
    for yr in sorted(set(res["ret_eq"].index.year)):
        s_a = res["ret_eq"][res["ret_eq"].index.year == yr]
        s_b = res["ret_iv"][res["ret_iv"].index.year == yr]
        rows.append({
            "year": yr,
            "ret_eq": s_a.sum(),
            "ret_iv": s_b.sum(),
            "sharpe_eq": _ann_sharpe(s_a),
            "sharpe_iv": _ann_sharpe(s_b),
        })
    df_yr = pd.DataFrame(rows).set_index("year")
    df_yr["sharpe_delta"] = df_yr["sharpe_iv"] - df_yr["sharpe_eq"]
    df_yr["ret_delta_pp"] = (df_yr["ret_iv"] - df_yr["ret_eq"]) * 100
    print(df_yr.round(3).to_string())
    pos_yrs = (df_yr["sharpe_delta"] > 0).sum()
    neg_yrs = (df_yr["sharpe_delta"] < 0).sum()
    print(f"  Years IV>EQ Sharpe: {pos_yrs}; Years IV<EQ: {neg_yrs}")
    print(f"  Sharpe-delta best year : {df_yr['sharpe_delta'].idxmax()} = {df_yr['sharpe_delta'].max():+.3f}")
    print(f"  Sharpe-delta worst year: {df_yr['sharpe_delta'].idxmin()} = {df_yr['sharpe_delta'].min():+.3f}")

    # 3) Estimation noise: how wildly do IV weights swing month-to-month?
    w_iv = res["weights_iv"]
    # For each month, compute change in weight per held-name (relative to prior month)
    # using L1 turnover but conditional on the name being held in BOTH months.
    held = (w_iv > 0).astype(int)
    same_held = (held & held.shift(1).fillna(0).astype(int))
    # Per-name absolute weight change among names held both months
    same_held_diff = (w_iv.diff().abs() * same_held).sum(axis=1) / same_held.sum(axis=1).replace(0, np.nan)
    # Compare to equal-weight (always 0.20 -> diff=0 for held names)
    print()
    print("FAILURE-MODE 3: Estimation noise (IV weight swings month-to-month)")
    print(f"  Mean |Δw| per name held both months (IV): {same_held_diff.mean():.4f}")
    print(f"  Std  |Δw| per name held both months (IV): {same_held_diff.std():.4f}")
    print(f"  Max  |Δw| per name held both months (IV): {same_held_diff.max():.4f}")
    print(f"  (Equal-weight baseline would be 0.0000 -- weight is always 0.2)")
    # Total turnover comparison
    to_eq = res["weights_eq"].diff().abs().sum(axis=1)
    to_iv = res["weights_iv"].diff().abs().sum(axis=1)
    print(f"  Avg monthly turnover EQ: {to_eq.mean():.4f}   IV: {to_iv.mean():.4f}")


if __name__ == "__main__":
    main()
