"""Cash-park overlay: route residual cash into a benchmark ETF (default
SPY) on days when the portfolio sits below max gross exposure.

Why this exists:
    The trader runs at MAX_GROSS_EXPOSURE=0.95 in theory but in practice
    finishes any given session at ~60-70% deployed because the vol-target
    overlay scales gross down on elevated VIX and the alpha sleeve is
    capped by sleeve allocator decisions. The leftover 30-40% sits in
    cash earning zero (Alpaca paper) or ~5% (Public.com HYSA). On a day
    SPY moves +0.8%, that idle cash costs 30-40 bps of relative perf.

    The fix is structural, not a backtest-tuned parameter: park excess
    cash in SPY (or a configurable benchmark). This converts the cash
    bucket from beta-0 to beta-~1 while leaving the alpha sleeve
    untouched. On up days we keep pace with SPY in the unallocated
    bucket; on down days we lose the cash cushion. The drawdown-aware
    overlay handles the down-days problem by suppressing cash-park
    when the drawdown tier escalates beyond GREEN — in that regime
    the cash IS the protection and we shouldn't replace it.

Trade-offs (be honest with the operator):
  + Removes ~30-40 bps of daily cash drag on up days.
  + Raises CAGR by approximately `avg_cash_pct × E[bench_return]`
    (analytic — for 35% avg cash × 10% SPY long-run = +3.5% CAGR).
  - Adds market beta to the residual bucket. On crash days we lose
    the cash cushion (mitigated by drawdown-tier gate below).
  - Adds rebalancing turnover on a benchmark ETF (taxable account
    matters — TLH overlay will treat SPY like any other holding).

Default OFF. Enable via env var `CASH_PARK_TICKER=SPY`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class CashParkPlan:
    ticker: str               # benchmark ticker (e.g. "SPY"); "" if disabled
    park_pct: float           # fraction of total equity to park in `ticker`
    reason: str               # operator-readable explanation
    active: bool              # True iff the overlay actually allocates

    def __bool__(self) -> bool:
        return self.active


def plan_cash_park(
    final_targets: dict[str, float],
    drawdown_pct: float = 0.0,
    *,
    cash_park_ticker: str | None = None,
    min_buffer: float = 0.05,
) -> CashParkPlan:
    """Decide whether to park residual cash in a benchmark ETF.

    Args:
      final_targets: current target weights, summing to <= MAX_GROSS.
      drawdown_pct: signed drawdown from peak (e.g. -0.07 = -7%). Used
                    to evaluate the drawdown tier — overlay only fires
                    in GREEN (no drawdown beyond YELLOW threshold).
      cash_park_ticker: override the env-var ticker (for testing).
      min_buffer: minimum cash fraction to ALWAYS keep liquid. Defaults
                  to 5% so we never go fully invested via the overlay.

    Returns:
      CashParkPlan. Inspect `.active`; if True, add `.park_pct` to
      `final_targets[.ticker]` before rebalancing.
    """
    ticker = (cash_park_ticker or os.environ.get("CASH_PARK_TICKER", ""))
    ticker = ticker.upper().strip()
    if not ticker:
        return CashParkPlan("", 0.0,
                            "disabled (CASH_PARK_TICKER unset)",
                            active=False)

    # Drawdown gate: in any tier worse than GREEN, the cash IS the
    # protection. Don't replace it with SPY exposure.
    from .risk_manager import evaluate_drawdown_tier
    tier = evaluate_drawdown_tier(drawdown_pct)
    if tier.name != "GREEN":
        return CashParkPlan(ticker, 0.0,
                            f"suppressed: drawdown tier {tier.name} "
                            f"({drawdown_pct:.1%})",
                            active=False)

    deployed = sum(final_targets.values())
    residual = 1.0 - deployed
    park_pct = residual - min_buffer
    # Require at least 1% to bother trading — anything smaller is
    # turnover noise. Also robust against FP residuals like 4.16e-17.
    MIN_TRADE_PCT = 0.01
    if park_pct < MIN_TRADE_PCT:
        return CashParkPlan(ticker, 0.0,
                            f"residual {residual:.1%} after "
                            f"{min_buffer:.1%} buffer = "
                            f"{max(park_pct, 0):.2%} (< 1% min, skip)",
                            active=False)

    return CashParkPlan(ticker, park_pct,
                        f"park {park_pct:.1%} in {ticker} "
                        f"(residual {residual:.1%}, "
                        f"buffer {min_buffer:.1%})",
                        active=True)
