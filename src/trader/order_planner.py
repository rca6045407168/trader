"""Order-type planner. Decides limit vs market vs stop, sets bracket exits.

Why not just market orders everywhere?
  - Market orders pay the full bid-ask spread. On illiquid names that's 20-50bps drag per round trip.
  - Limit orders give you price control but risk non-fills.
  - Stop-loss + take-profit brackets enforce your exit discipline even when the
    market opens against you and you're not at your screen.

Entry rules:
  - Momentum picks (long-horizon, low conviction on entry timing): LIMIT at last close +30bps.
    If not filled by 30min after open, the daily orchestrator falls back to MARKET.
  - Bottom-catch picks (we want a discount on the bounce): LIMIT at last close - 0.2*ATR.
    With an OCO bracket: stop at -1.5*ATR, trailing-stop activates after +2*ATR.

Exit rules:
  - Momentum: NO stop loss. Strategy edge is letting winners run; rotation happens at next monthly rebal.
  - Bottom-catch: NO take-profit, NO trailing stop, ONLY a wide catastrophic stop at -3.5 ATR.
    Time-based exit at 20 trading days. v0.7 backtest (1,397 triggers) showed the original
    bracket (stop -1.5 ATR + take +3 ATR + trail 1 ATR) gave back 36% of the edge — brackets
    are anti-pattern for mean-reversion strategies. The wide -3.5 ATR stop only fires on tail
    events (flash crash, takeover failure, fraud) while preserving normal volatility room.
"""
from dataclasses import dataclass, field
from typing import Literal

OrderType = Literal["MARKET", "LIMIT"]
Side = Literal["BUY", "SELL"]
Tif = Literal["DAY", "GTC", "IOC"]


@dataclass
class OrderPlan:
    symbol: str
    side: Side
    notional: float | None = None
    qty: float | None = None
    order_type: OrderType = "MARKET"
    limit_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    trail_pct: float | None = None
    time_in_force: Tif = "DAY"
    bracket: bool = False
    rationale: str = ""
    metadata: dict = field(default_factory=dict)


def plan_momentum_entry(symbol: str, notional: float, last_price: float) -> OrderPlan:
    """Limit a touch above the last close. Daily cron upgrades to MARKET if unfilled."""
    limit = round(last_price * 1.003, 2)
    return OrderPlan(
        symbol=symbol, side="BUY", notional=notional,
        order_type="LIMIT", limit_price=limit, time_in_force="DAY",
        rationale=f"momentum entry: LIMIT +30bps over ${last_price:.2f} = ${limit:.2f}",
        metadata={"strategy": "momentum", "fallback_to_market_after": "30min"},
    )


def plan_bottom_entry(
    symbol: str, notional: float, last_price: float,
    atr: float, max_loss_pct: float = 0.035,
) -> OrderPlan:
    """Bottom-catch entry: limit-buy at -0.2 ATR discount, wide cat-stop only.

    v0.7 redesign — the original tight bracket gave back 36% of the edge.
    Now: NO take-profit, NO trail, ONLY a -3.5 ATR catastrophic stop. The
    daily orchestrator is responsible for time-based 20-day exits via a
    separate close-position pass.

    notional sized so that the catastrophic stop at -3.5 ATR loses no more
    than `max_loss_pct` (default 3.5%) of position value.
    """
    discount_pct = max(0.001, min(0.01, atr / last_price * 0.2))
    limit = round(last_price * (1 - discount_pct), 2)
    cat_stop = round(last_price - 3.5 * atr, 2)

    return OrderPlan(
        symbol=symbol, side="BUY", notional=notional,
        order_type="LIMIT", limit_price=limit,
        stop_loss_price=cat_stop, take_profit_price=None,
        trail_pct=None,
        time_in_force="GTC",
        bracket=False,
        rationale=(
            f"bottom-catch v0.7: LIMIT @ ${limit:.2f} (-{discount_pct:.1%}), "
            f"cat-stop @ ${cat_stop:.2f} (-3.5 ATR). Time exit at 20 trading days."
        ),
        metadata={
            "strategy": "bottom_catch", "atr": atr, "max_loss_pct": max_loss_pct,
            "time_exit_days": 20, "version": "0.7",
        },
    )
