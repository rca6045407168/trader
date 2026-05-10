"""Alpaca order placement.

Two modes:
  - place_target_weights(): rebalance to portfolio %s using notional MARKET orders.
    Used for the monthly momentum sleeve where exact entry price doesn't matter.
  - place_bracket_order(): single LIMIT order with stop-loss + take-profit + trail.
    Used for bottom-catch entries where we want price discipline + auto-exits.

Paper trading by default. Switch to live by setting ALPACA_PAPER=false in .env.
"""
from datetime import datetime

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


def close_aged_bottom_catches(max_age_days: int = 20, dry_run: bool = False) -> list[dict]:
    """v1.3 (B1 FIX): close open bottom-catch LOTS older than max_age_days.

    The previous version queried the orders table by ticker, which mis-targeted
    momentum positions whenever a ticker had ever been a bottom-catch. The fix
    is to query the new position_lots table (sleeve-tagged at open) and only
    close lots that were actually opened by the BOTTOM_CATCH sleeve.

    For symbols with mixed sleeves (e.g. NVDA held by both momentum and
    bottom-catch), this only closes the qty corresponding to bottom-catch lots
    by submitting a SELL of that exact qty rather than close_position().
    """
    # v6.0.x: close_lots_auto reads TLH_LOT_SELECTION env (FIFO or
    # HIFO). Default FIFO preserves prior behaviour; setting HIFO
    # multiplies harvested loss by ~20-40% in the steady state.
    from .journal import open_lots_for_sleeve, close_lots_auto as close_lots_fifo

    if dry_run:
        return []

    aged_lots = open_lots_for_sleeve("BOTTOM_CATCH", max_age_days=max_age_days)
    if not aged_lots:
        return []

    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = get_client()
    open_positions = {p.symbol: float(p.qty) for p in client.get_all_positions()}
    out = []

    # Group aged lots by symbol
    by_symbol: dict[str, float] = {}
    by_symbol_lots: dict[str, list] = {}
    for lot in aged_lots:
        by_symbol[lot["symbol"]] = by_symbol.get(lot["symbol"], 0) + lot["qty"]
        by_symbol_lots.setdefault(lot["symbol"], []).append(lot)

    for sym, qty_to_close in by_symbol.items():
        held = open_positions.get(sym, 0)
        if held <= 0:
            # We have lots in our journal but no actual position — maybe stop fired
            # without us tracking it. Mark lots closed at last known price (best effort).
            try:
                last = get_last_price(sym)
                close_lots_fifo(sym, "BOTTOM_CATCH", qty_to_close, last)
                out.append({"symbol": sym, "action": "orphan_lots_reconciled", "qty": qty_to_close})
            except Exception as e:
                out.append({"symbol": sym, "action": "orphan_lots_reconciled", "status": "error", "error": str(e)})
            continue

        sell_qty = min(qty_to_close, held)
        try:
            order = client.submit_order(MarketOrderRequest(
                symbol=sym, qty=sell_qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            ))
            # Close lots in journal at the (estimated) fill price
            try:
                last = get_last_price(sym)
            except Exception:
                last = 0.0
            close_lots_fifo(sym, "BOTTOM_CATCH", sell_qty, last, str(order.id))
            out.append({
                "symbol": sym, "action": "time_exit_20d",
                "qty": sell_qty, "order_id": str(order.id), "status": "submitted",
            })
        except Exception as e:
            out.append({"symbol": sym, "action": "time_exit_20d", "status": "error", "error": str(e)})
    return out


def place_target_weights(
    targets: dict[str, float], min_order_usd: float = 50.0, dry_run: bool = False,
    use_moc: bool = None,
) -> list[dict]:
    """Rebalance to target weights via notional market orders.

    targets: {ticker: portfolio_pct (0-1)}

    Closes positions not in targets. Skips orders below min_order_usd.

    use_moc (v3.59.0): if True, route as MarketOnClose (TimeInForce.CLS)
    instead of regular market DAY orders. Closing-auction prints typically
    add 0-2bp slippage vs 5-10bp on a market order placed mid-session,
    saving ~30-50bps/yr at 60% monthly turnover. Defaults to env-flag
    USE_MOC_ORDERS=true. Only works if cron runs > ~15:30 ET — orders
    submitted after the close-cutoff (15:50 ET on most brokers) will
    reject; system falls back to DAY automatically per Alpaca behavior.
    """
    if use_moc is None:
        import os
        use_moc = os.getenv("USE_MOC_ORDERS", "false").lower() == "true"
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

    # v3.58.1 — SlippageTracker SHADOW logging. Best-effort, never blocks.
    def _log_slip(symbol: str, side: str, decision_mid: float,
                  notional: float):
        try:
            from .v358_world_class import SlippageTracker
            from .journal import _conn
            sl = SlippageTracker()
            if sl.status() not in ("LIVE", "SHADOW"):
                return
            # We don't have fill price yet at submit — log decision_mid only;
            # reconcile.py will close the loop with the actual filled_avg.
            with _conn() as c:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS slippage_log ("
                    "ts TEXT, symbol TEXT, side TEXT, decision_mid REAL, "
                    "notional REAL, fill_price REAL, slippage_bps REAL, "
                    "status TEXT)"
                )
                c.execute(
                    "INSERT INTO slippage_log "
                    "(ts, symbol, side, decision_mid, notional, status) "
                    "VALUES (?,?,?,?,?,?)",
                    (datetime.utcnow().isoformat(), symbol, side,
                     decision_mid, notional, sl.status()),
                )
        except Exception:
            pass

    from .journal import open_lot
    for symbol, target_pct in targets.items():
        target_value = equity * target_pct
        current_value = positions.get(symbol, 0.0)
        delta = target_value - current_value
        if abs(delta) < min_order_usd:
            out.append({"symbol": symbol, "side": "skip", "status": "below_min"})
            continue
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        # v3.59.0: route as MOC if requested. CLS time-in-force = closing
        # auction print, lower expected slippage on liquid names.
        tif = TimeInForce.CLS if use_moc else TimeInForce.DAY
        req = MarketOrderRequest(
            symbol=symbol, notional=round(abs(delta), 2),
            side=side, time_in_force=tif,
        )
        # v3.58.1 SlippageTracker — capture decision-mid pre-submit
        try:
            decision_mid = get_last_price(symbol)
        except Exception:
            decision_mid = 0.0
        _log_slip(symbol, side.value, decision_mid, abs(delta))
        try:
            order = client.submit_order(req)
            order_id = str(order.id)
            out.append({
                "symbol": symbol, "side": side.value,
                "notional": round(abs(delta), 2),
                "order_id": order_id, "status": "submitted",
            })
            # v1.9 (B7 fix): track momentum positions in lots so reconcile works.
            # We don't know the fill price yet at submit time — use last_price as estimate.
            if side == OrderSide.BUY:
                try:
                    qty_est = round(abs(delta) / decision_mid, 4) if decision_mid > 0 else 0
                    if qty_est > 0:
                        open_lot(symbol, "MOMENTUM", qty=qty_est, open_price=decision_mid, open_order_id=order_id)
                except Exception:
                    pass  # journal write is best-effort
        except Exception as e:
            out.append({
                "symbol": symbol, "side": side.value,
                "notional": round(abs(delta), 2),
                "status": "error", "error": str(e),
            })
    return out


def backfill_momentum_lots_from_positions() -> list[dict]:
    """v1.9 one-shot helper: write position_lots rows for any Alpaca momentum positions
    that aren't already tracked. Used to repair the B7 gap on existing accounts.
    """
    from .journal import _conn, open_lot
    client = get_client()
    positions = client.get_all_positions()
    out = []
    with _conn() as c:
        already = {r["symbol"] for r in c.execute(
            "SELECT DISTINCT symbol FROM position_lots WHERE closed_at IS NULL"
        ).fetchall()}
    for p in positions:
        if p.symbol in already:
            out.append({"symbol": p.symbol, "action": "skipped_already_tracked"})
            continue
        # Tag as MOMENTUM by default (correct for current state since all 5 paper
        # positions are momentum picks). Bottom-catch positions would be tagged
        # at order time after this fix.
        lot_id = open_lot(
            p.symbol, "MOMENTUM",
            qty=float(p.qty),
            open_price=float(p.avg_entry_price),
            open_order_id=None,
        )
        out.append({"symbol": p.symbol, "action": "backfilled", "lot_id": lot_id,
                    "qty": float(p.qty), "avg_entry": float(p.avg_entry_price)})
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
    elif plan.stop_loss_price and not plan.take_profit_price:
        # OTO order class: parent + single stop child (no take). Used for v0.7 bottom-catch.
        common["order_class"] = OrderClass.OTO
        common["stop_loss"] = StopLossRequest(stop_price=plan.stop_loss_price)

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
