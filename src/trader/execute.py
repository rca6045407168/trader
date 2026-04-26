"""Alpaca order placement.

Two modes:
  - place_target_weights(): rebalance to portfolio %s using notional MARKET orders.
    Used for the monthly momentum sleeve where exact entry price doesn't matter.
  - place_bracket_order(): single LIMIT order with stop-loss + take-profit + trail.
    Used for bottom-catch entries where we want price discipline + auto-exits.

Paper trading by default. Switch to live by setting ALPACA_PAPER=false in .env.
"""
from .config import ALPACA_KEY, ALPACA_SECRET, ALPACA_PAPER
from .order_planner import OrderPlan

_client = None
_data_client = None


def get_client():
    global _client
    if _client is None:
        if not ALPACA_KEY or not ALPACA_SECRET:
            raise RuntimeError(
                "Alpaca keys missing. Set ALPACA_API_KEY and ALPACA_API_SECRET in .env. "
                "Sign up free at https://alpaca.markets and switch to paper trading."
            )
        from alpaca.trading.client import TradingClient
        _client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=ALPACA_PAPER)
    return _client


def _get_data_client():
    global _data_client
    if _data_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _data_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
    return _data_client


def get_last_price(symbol: str) -> float:
    """Latest trade price from Alpaca."""
    from alpaca.data.requests import StockLatestTradeRequest
    client = _get_data_client()
    resp = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
    return float(resp[symbol].price)


def place_target_weights(
    targets: dict[str, float], min_order_usd: float = 50.0, dry_run: bool = False,
) -> list[dict]:
    """Rebalance to target weights via notional market orders.

    targets: {ticker: portfolio_pct (0-1)}

    Closes positions not in targets. Skips orders below min_order_usd.
    """
    if dry_run:
        return [{"symbol": s, "target_pct": w, "status": "dry_run"} for s, w in targets.items()]

    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = get_client()
    account = client.get_account()
    equity = float(account.equity)
    positions = {p.symbol: float(p.market_value) for p in client.get_all_positions()}

    out: list[dict] = []

    for symbol in list(positions.keys()):
        if symbol not in targets:
            try:
                client.close_position(symbol)
                out.append({"symbol": symbol, "side": "close", "status": "closed"})
            except Exception as e:
                out.append({"symbol": symbol, "side": "close", "status": "error", "error": str(e)})

    for symbol, target_pct in targets.items():
        target_value = equity * target_pct
        current_value = positions.get(symbol, 0.0)
        delta = target_value - current_value
        if abs(delta) < min_order_usd:
            out.append({"symbol": symbol, "side": "skip", "status": "below_min"})
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol, notional=round(abs(delta), 2),
            side=side, time_in_force=TimeInForce.DAY,
        )
        try:
            order = client.submit_order(req)
            out.append({
                "symbol": symbol, "side": side.value,
                "notional": round(abs(delta), 2),
                "order_id": str(order.id), "status": "submitted",
            })
        except Exception as e:
            out.append({
                "symbol": symbol, "side": side.value,
                "notional": round(abs(delta), 2),
                "status": "error", "error": str(e),
            })
    return out


def place_bracket_order(plan: OrderPlan, dry_run: bool = False) -> dict:
    """Single bracketed limit order with stop-loss + take-profit + trail.

    Alpaca's bracket-order class atomically attaches OCO exits to the parent.
    """
    if dry_run:
        return {
            "symbol": plan.symbol, "status": "dry_run",
            "plan": {
                "order_type": plan.order_type, "limit": plan.limit_price,
                "stop": plan.stop_loss_price, "take": plan.take_profit_price,
                "trail_pct": plan.trail_pct,
            },
        }

    from alpaca.trading.requests import (
        LimitOrderRequest, MarketOrderRequest,
        StopLossRequest, TakeProfitRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    client = get_client()
    side = OrderSide.BUY if plan.side == "BUY" else OrderSide.SELL
    tif = getattr(TimeInForce, plan.time_in_force)

    # Notional + bracket isn't supported by Alpaca — need qty for brackets.
    # Convert notional -> qty via last price.
    qty = plan.qty
    if qty is None and plan.notional is not None:
        last = get_last_price(plan.symbol)
        # Round down so we don't overrun notional
        qty = max(1, int(plan.notional / last))

    common = {
        "symbol": plan.symbol,
        "qty": qty,
        "side": side,
        "time_in_force": tif,
    }

    if plan.bracket and plan.stop_loss_price and plan.take_profit_price:
        common["order_class"] = OrderClass.BRACKET
        common["stop_loss"] = StopLossRequest(stop_price=plan.stop_loss_price)
        common["take_profit"] = TakeProfitRequest(limit_price=plan.take_profit_price)

    if plan.order_type == "LIMIT" and plan.limit_price:
        req = LimitOrderRequest(limit_price=plan.limit_price, **common)
    else:
        req = MarketOrderRequest(**common)

    try:
        order = client.submit_order(req)
        return {
            "symbol": plan.symbol, "qty": qty, "side": plan.side,
            "order_type": plan.order_type, "limit": plan.limit_price,
            "stop": plan.stop_loss_price, "take": plan.take_profit_price,
            "order_id": str(order.id), "status": "submitted",
            "rationale": plan.rationale,
        }
    except Exception as e:
        return {
            "symbol": plan.symbol, "qty": qty,
            "status": "error", "error": str(e),
            "rationale": plan.rationale,
        }
