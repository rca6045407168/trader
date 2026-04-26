"""Walk-forward meta-optimizer. The cure for overfitting.

The failure mode of every retail algo: try 50 parameter combos, pick the best,
deploy it, watch it fail live. The 'best' was just lucky on the in-sample data.

Walk-forward fixes this:
  1. Train on years 2015-2020 — sweep parameter grid, pick best in-sample.
  2. Validate that pick on 2021-2024 (UNSEEN). If out-sample Sharpe < in-sample
     Sharpe by more than 50%, the strategy is curve-fit — do not deploy.
  3. Pick the parameter set that's most STABLE across both periods, not the
     single best in-sample.

We expose two levers: lookback months and top_N. More dimensions = more
overfitting risk. Resist the urge to tune everything.
"""
from itertools import product
import pandas as pd

from .backtest import backtest_momentum
from .universe import DEFAULT_LIQUID_50


def walk_forward(
    universe: list[str] | None = None,
    train_start: str = "2015-01-01",
    train_end: str = "2020-12-31",
    test_start: str = "2021-01-01",
    test_end: str = "2025-12-31",
    lookback_months_grid: tuple[int, ...] = (3, 6, 9, 12),
    top_n_grid: tuple[int, ...] = (5, 10, 15, 20),
) -> pd.DataFrame:
    """Sweep params on train, evaluate on holdout test. Returns ranked DataFrame."""
    universe = universe or DEFAULT_LIQUID_50
    rows = []
    for L, N in product(lookback_months_grid, top_n_grid):
        try:
            ins = backtest_momentum(universe, train_start, train_end,
                                    lookback_months=L, top_n=N).stats()
            oos = backtest_momentum(universe, test_start, test_end,
                                    lookback_months=L, top_n=N).stats()
        except Exception as e:
            print(f"  failed L={L} N={N}: {e}")
            continue
        decay = (ins["sharpe"] - oos["sharpe"]) / ins["sharpe"] if ins["sharpe"] else float("nan")
        rows.append({
            "lookback_M": L, "top_N": N,
            "in_sharpe": ins["sharpe"], "in_cagr": ins["cagr"],
            "out_sharpe": oos["sharpe"], "out_cagr": oos["cagr"],
            "out_alpha": oos["alpha"], "out_maxdd": oos["max_drawdown"],
            "sharpe_decay": round(decay, 3),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("out_sharpe", ascending=False).reset_index(drop=True)
    return df


def recommend_params(walk_df: pd.DataFrame) -> dict:
    """Pick the most STABLE param set, not the highest in-sample.

    Rule: out_sharpe > 0.5 AND sharpe_decay < 0.5 (less than 50% decay).
    Among survivors, pick highest out_sharpe. If none survive: ABSTAIN.
    """
    if walk_df.empty:
        return {"recommendation": "ABSTAIN", "reason": "no successful backtests"}
    survivors = walk_df[(walk_df["out_sharpe"] > 0.5) & (walk_df["sharpe_decay"] < 0.5)]
    if survivors.empty:
        return {
            "recommendation": "ABSTAIN",
            "reason": "no parameter set passes overfitting check (all show >50% Sharpe decay or <0.5 out-sample Sharpe)",
            "best_attempt": walk_df.iloc[0].to_dict() if len(walk_df) else None,
        }
    best = survivors.iloc[0]
    return {
        "recommendation": "DEPLOY",
        "lookback_months": int(best["lookback_M"]),
        "top_n": int(best["top_N"]),
        "out_sharpe": float(best["out_sharpe"]),
        "out_cagr": float(best["out_cagr"]),
        "sharpe_decay": float(best["sharpe_decay"]),
    }
