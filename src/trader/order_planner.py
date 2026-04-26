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
  - Bottom-catch: hard stop at -1.5*ATR (cuts losers fast — mean reversion that doesn't revert IS the failure mode).
    Trailing stop at 1*ATR once trade is up 2*ATR — lock in profit on the bounce, give it room to run.
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
    atr: float, max_loss_pct: float = 0.015,
) -> OrderPlan:
    """Bracket-order limit entry with stop loss + trailing.

    notional sized so that a stop-loss at -1.5 ATR loses no more than
    `max_loss_pct` of total portfolio (caller still applies its own cap).
    """
    discount_pct = max(0.001, min(0.01, atr / last_price * 0.2))
    limit = round(last_price * (1 - discount_pct), 2)
    stop = round(last_price - 1.5 * atr, 2)
    take = round(last_price + 3.0 * atr, 2)
    trail_pct = max(1.0, min(5.0, (atr / last_price) * 100))

    return OrderPlan(
        symbol=symbol, side="BUY", notional=notional,
        order_type="LIMIT", limit_price=limit,
        stop_loss_price=stop, take_profit_price=take,
        trail_pct=round(trail_pct, 2),
        time_in_force="DAY", bracket=True,
        rationale=(
            f"bottom-catch: LIMIT @ ${limit:.2f} (-{discount_pct:.1%}), "
            f"stop @ ${stop:.2f} (-1.5 ATR), take @ ${take:.2f} (+3 ATR), trail {trail_pct:.1f}%"
        ),
        metadata={"strategy": "bottom_catch", "atr": atr, "max_loss_pct": max_loss_pct},
    )
