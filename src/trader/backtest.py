"""Pandas-only backtest. Transparent over fast — every line auditable.

We compute a monthly-rebalanced top-N momentum portfolio with realistic
slippage on turnover, then benchmark against SPY buy-and-hold.
"""
from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
import pandas as pd

from .data import fetch_history
from .config import REPORT_DIR


@dataclass
class BacktestResult:
    equity: pd.Series
    monthly_returns: pd.Series
    benchmark_equity: pd.Series
    weights: pd.DataFrame

    def stats(self) -> dict:
        years = max((self.equity.index[-1] - self.equity.index[0]).days / 365.25, 1e-9)
        cagr = (self.equity.iloc[-1] / self.equity.iloc[0]) ** (1 / years) - 1
        bench_cagr = (self.benchmark_equity.iloc[-1] / self.benchmark_equity.iloc[0]) ** (1 / years) - 1
        ann_vol = self.monthly_returns.std() * math.sqrt(12)
        sharpe = (self.monthly_returns.mean() * 12) / ann_vol if ann_vol > 0 else 0.0
        running_max = self.equity.cummax()
        drawdown = (self.equity / running_max - 1)
        max_dd = drawdown.min()
        bench_max = self.benchmark_equity.cummax()
        bench_dd = (self.benchmark_equity / bench_max - 1).min()

        return {
            "start": str(self.equity.index[0].date()),
            "end": str(self.equity.index[-1].date()),
            "years": round(years, 2),
            "final_equity": round(float(self.equity.iloc[-1]), 2),
            "cagr": round(float(cagr), 4),
            "benchmark_cagr": round(float(bench_cagr), 4),
            "alpha": round(float(cagr - bench_cagr), 4),
            "sharpe": round(float(sharpe), 3),
            "max_drawdown": round(float(max_dd), 4),
            "benchmark_max_drawdown": round(float(bench_dd), 4),
            "avg_monthly_return": round(float(self.monthly_returns.mean()), 4),
            "win_rate": round(float((self.monthly_returns > 0).mean()), 3),
        }


def backtest_momentum(
    universe: list[str],
    start: str = "2015-01-01",
    end: str | None = None,
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    initial_capital: float = 100_000.0,
    slippage_bps: float = 5.0,
    regime_filter: str | None = None,
    regime_fast_ma: int = 50,
    regime_slow_ma: int = 200,
) -> BacktestResult:
    """Monthly-rebalanced top-N momentum.

    regime_filter:
      None        — always invested.
      "slow_ma"   — only invested when SPY > slow MA (default 200d). Whipsaw-prone.
      "cross"     — only invested when SPY fast-MA (50d) > slow-MA (200d). "Golden cross" rule.
                    Slower to enter and exit, fewer whipsaws than slow_ma.
      "smooth"    — weight = clip((SPY/slow_ma - 1) * 10, 0, 1). Gradient instead of binary.
    """
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    prices = fetch_history(universe, start=start, end=end)
    prices = prices.dropna(axis=1, thresh=int(len(prices) * 0.5))

    monthly = prices.resample("ME").last().ffill(limit=2)
    monthly_ret = monthly.pct_change()

    L, S = lookback_months, skip_months
    lookback = monthly.shift(S) / monthly.shift(S + L) - 1

    weights = pd.DataFrame(0.0, index=monthly.index, columns=monthly.columns)
    for d in monthly.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_n:
            continue
        winners = scores.nlargest(top_n).index
        weights.loc[d, winners] = 1.0 / top_n

    spy_full = fetch_history(["SPY"], start=start, end=end)["SPY"]

    if regime_filter:
        spy_slow = spy_full.rolling(regime_slow_ma).mean()
        if regime_filter == "slow_ma":
            in_regime = (spy_full > spy_slow).astype(float)
        elif regime_filter == "cross":
            spy_fast = spy_full.rolling(regime_fast_ma).mean()
            in_regime = (spy_fast > spy_slow).astype(float)
        elif regime_filter == "smooth":
            in_regime = ((spy_full / spy_slow - 1) * 10).clip(0, 1)
        else:
            raise ValueError(f"unknown regime_filter: {regime_filter}")
        in_regime_monthly = in_regime.resample("ME").last().reindex(monthly.index).fillna(0)
        weights = weights.mul(in_regime_monthly, axis=0)

    turnover = weights.diff().abs().sum(axis=1).fillna(0)
    slippage_drag = turnover * (slippage_bps / 10_000)
    gross_ret = (weights.shift(1) * monthly_ret).sum(axis=1)
    net_ret = gross_ret - slippage_drag

    equity = (1 + net_ret.fillna(0)).cumprod() * initial_capital

    spy_monthly = spy_full.resample("ME").last()
    spy_ret = spy_monthly.pct_change().fillna(0)
    bench_equity = (1 + spy_ret).cumprod() * initial_capital

    return BacktestResult(equity=equity, monthly_returns=net_ret.fillna(0),
                          benchmark_equity=bench_equity, weights=weights)


def plot_equity(result: BacktestResult, name: str = "momentum") -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                    gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(result.equity.index, result.equity.values, label="Strategy", linewidth=2)
    ax1.plot(result.benchmark_equity.index, result.benchmark_equity.values,
             label="SPY (buy & hold)", linewidth=2, alpha=0.7)
    ax1.set_ylabel("Equity ($)")
    ax1.set_title(f"Momentum Strategy vs SPY ({result.equity.index[0].date()} to {result.equity.index[-1].date()})")
    ax1.legend()
    ax1.grid(alpha=0.3)

    running_max = result.equity.cummax()
    drawdown = (result.equity / running_max - 1) * 100
    ax2.fill_between(drawdown.index, drawdown.values, 0, alpha=0.4, color="red")
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.grid(alpha=0.3)

    out = REPORT_DIR / f"{name}_equity.png"
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    plt.close()
    return out
