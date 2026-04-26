"""Unit tests for order_planner. Validates v0.7 redesign — wide cat-stop, no take."""
from trader.order_planner import plan_momentum_entry, plan_bottom_entry


def test_momentum_limit_above_close():
    plan = plan_momentum_entry("AAPL", notional=5000, last_price=180.00)
    assert plan.symbol == "AAPL"
    assert plan.side == "BUY"
    assert plan.order_type == "LIMIT"
    assert plan.limit_price > 180.00
    assert plan.limit_price < 181.00  # +30bps = ~180.54
    assert plan.stop_loss_price is None  # momentum has NO stop
    assert plan.bracket is False


def test_bottom_entry_v07():
    """v0.7 design: limit at -0.2 ATR, NO take, NO trail, ONLY -3.5 ATR cat-stop, 20d time exit."""
    last_price = 100.00
    atr = 2.50  # ATR = 2.5% of price
    plan = plan_bottom_entry("NVDA", notional=5000, last_price=last_price, atr=atr)
    assert plan.side == "BUY"
    assert plan.order_type == "LIMIT"
    assert plan.limit_price < last_price  # discount
    assert plan.take_profit_price is None  # v0.7: no take
    assert plan.trail_pct is None  # v0.7: no trail
    assert plan.stop_loss_price is not None
    # cat-stop should be at -3.5 ATR = $100 - $8.75 = $91.25
    assert 91.0 < plan.stop_loss_price < 91.5
    assert plan.metadata["time_exit_days"] == 20
    assert plan.metadata["version"] == "0.7"


def test_bottom_entry_high_vol():
    """High-ATR stocks should still produce reasonable plans."""
    plan = plan_bottom_entry("TSLA", notional=3000, last_price=200, atr=10.0)
    assert plan.stop_loss_price < plan.limit_price
    # cat-stop at $200 - $35 = $165
    assert 164.5 < plan.stop_loss_price < 165.5
