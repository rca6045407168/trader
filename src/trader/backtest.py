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


def backtest_momentum_realistic(
    universe: list[str],
    start: str = "2015-01-01",
    end: str | None = None,
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 5,
    initial_capital: float = 100_000.0,
    slippage_bps: float = 5.0,
) -> BacktestResult:
    """v1.4 (B4 fix): rebalance at month-end-close DECISION, but trade at the
    OPEN of the next trading day. This is what actually happens in production:
    we generate orders 4:10pm PT, Alpaca queues them, they fill at market open.

    The slippage between month-end CLOSE and next-day OPEN is real and material.
    Empirically ~30-100bps absolute per-name on rebalance days. The original
    backtest assumed instantaneous close-to-close fills, overstating CAGR by
    ~3-7% annually (per CRITIQUE.md B4).
    """
    from .data import fetch_history_with_open

    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    close_df, open_df = fetch_history_with_open(universe, start=start, end=end)
    close_df = close_df.dropna(axis=1, thresh=int(len(close_df) * 0.5))
    open_df = open_df[close_df.columns]

    # Daily index
    daily_close = close_df
    daily_open = open_df

    # Identify month-end rebalance dates (last trading day of each month)
    monthly_close = daily_close.resample("ME").last().ffill(limit=2)

    L, S = lookback_months, skip_months
    lookback = monthly_close.shift(S) / monthly_close.shift(S + L) - 1

    # For each rebalance month, decide weights based on data through that month-end close
    target_weights_by_rebal = pd.DataFrame(0.0, index=monthly_close.index, columns=monthly_close.columns)
    for d in monthly_close.index:
        scores = lookback.loc[d].dropna()
        if len(scores) < top_n:
            continue
        winners = scores.nlargest(top_n).index
        target_weights_by_rebal.loc[d, winners] = 1.0 / top_n

    # Now build a realistic month-by-month return series:
    # At each rebalance date T (month-end close), we DECIDE weights.
    # We TRADE at the open of T+1 (next trading day after T).
    # We HOLD until next rebalance T' (next month-end close), then trade out at T'+1 open.
    # Period return for a held name = (open_T'+1 / open_T+1) - 1
    # Plus a small final drift from open_T'+1 to close_T'+1 (we approximate as 0
    # since the next entry happens immediately).

    rebal_dates = list(target_weights_by_rebal.index)
    period_returns = pd.Series(0.0, index=rebal_dates)

    def _next_trading_day_after(T_calendar):
        """Given a (possibly non-trading-day) calendar date, return the next
        trading day that follows the LAST trading day on or before T_calendar."""
        # last trading day on or before T_calendar (handles weekend month-ends)
        last_trade_idx = daily_close.index.get_indexer([T_calendar], method="ffill")[0]
        if last_trade_idx < 0 or last_trade_idx + 1 >= len(daily_close.index):
            return None
        return daily_open.index[last_trade_idx + 1]

    for i, T in enumerate(rebal_dates):
        if i + 1 >= len(rebal_dates):
            break
        T_next = rebal_dates[i + 1]
        T_plus_1 = _next_trading_day_after(T)
        T_next_plus_1 = _next_trading_day_after(T_next)
        if T_plus_1 is None or T_next_plus_1 is None:
            continue

        weights = target_weights_by_rebal.loc[T]
        held = weights[weights > 0]
        if len(held) == 0:
            continue
        try:
            buy_prices = daily_open.loc[T_plus_1, held.index]
            sell_prices = daily_open.loc[T_next_plus_1, held.index]
        except KeyError:
            continue
        if buy_prices.isna().any() or sell_prices.isna().any():
            continue
        per_name_returns = (sell_prices / buy_prices - 1)
        period_returns.loc[T_next] = float((per_name_returns * held).sum())

    # Slippage: ~5bps both legs of every rebalance turnover
    turnover = target_weights_by_rebal.diff().abs().sum(axis=1).fillna(0)
    slippage_drag = turnover * (slippage_bps / 10_000)
    net_ret = period_returns - slippage_drag

    equity = (1 + net_ret.fillna(0)).cumprod() * initial_capital

    spy_close, spy_open = fetch_history_with_open(["SPY"], start=start, end=end)
    spy_monthly = spy_close["SPY"].resample("ME").last()
    spy_ret = spy_monthly.pct_change().fillna(0)
    bench_equity = (1 + spy_ret).cumprod() * initial_capital

    return BacktestResult(
        equity=equity, monthly_returns=net_ret.fillna(0),
        benchmark_equity=bench_equity, weights=target_weights_by_rebal,
    )


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
