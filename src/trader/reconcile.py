"""Reconciliation. Compare what the journal SAYS we hold vs what Alpaca ACTUALLY shows.

A mismatch means one of:
  - An order didn't fill (check Alpaca order status)
  - An order filled but our journal log failed (check timestamp gaps)
  - A position closed via stop-loss without our knowledge (good — the OTO worked)
  - A bracket fired but our journal didn't record the fill

Run this every morning before placing new orders. If the diff is non-trivial,
HALT and require human review.
"""
import json
from .journal import recent_snapshots, _conn


def get_expected_positions_qty() -> dict[str, float]:
    """v1.9 fix: expected SHARE COUNT per symbol (not dollar value).

    Reconciliation should compare share counts (which don't change with market
    moves) not dollar values (which drift with price every minute). The previous
    qty * open_price formulation always mismatched by the unrealized P&L.
    """
    from .journal import _conn
    with _conn() as c:
        rows = c.execute(
            """SELECT symbol, qty FROM position_lots WHERE closed_at IS NULL"""
        ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        out[r["symbol"]] = out.get(r["symbol"], 0) + (r["qty"] or 0)
    return out


def get_expected_positions() -> dict[str, float]:
    """Legacy interface: expected DOLLAR value. Kept for backwards compatibility."""
    from .journal import _conn
    with _conn() as c:
        rows = c.execute(
            """SELECT symbol, qty, open_price FROM position_lots WHERE closed_at IS NULL"""
        ).fetchall()
    if rows:
        out: dict[str, float] = {}
        for r in rows:
            v = (r["qty"] or 0) * (r["open_price"] or 0)
            out[r["symbol"]] = out.get(r["symbol"], 0) + v
        return out
    snaps = recent_snapshots(days=2)
    if snaps:
        return json.loads(snaps[0]["positions_json"])
    return {}


def get_actual_positions_qty(client) -> dict[str, float]:
    """v1.9: actual SHARE COUNT per symbol (not market value)."""
    return {p.symbol: float(p.qty) for p in client.get_all_positions()}


def get_actual_positions(client) -> dict[str, float]:
    """Legacy: actual dollar market value."""
    return {p.symbol: float(p.market_value) for p in client.get_all_positions()}


def get_pending_orders_qty(client) -> dict[str, float]:
    """Open orders that haven't filled yet, keyed by symbol.

    v3.52.2 introduced this to distinguish orphan-lot bugs from
    awaiting-fill orders queued for the next session.

    v6.0.x: dual-path. If `client` is a BrokerAdapter (has
    `get_open_orders()`), use that — works for both Alpaca and
    Public.com. If `client` is the raw Alpaca TradingClient (legacy
    caller), fall back to the Alpaca-specific GetOrdersRequest path.

    Returns {symbol: qty_pending}, signed (BUY +, SELL −).
    """
    pending: dict[str, float] = {}
    # v6.0.x: detect a BrokerAdapter by reading broker_name as a real
    # string (MagicMock-based tests auto-generate attributes that
    # aren't strings, so the legacy Alpaca branch fires correctly for
    # mocked clients).
    broker_name = getattr(client, "broker_name", None)
    if (isinstance(broker_name, str)
            and hasattr(client, "get_open_orders")):
        try:
            for o in client.get_open_orders():
                qty = float(o.qty or 0)
                if o.side == "buy":
                    pending[o.symbol] = pending.get(o.symbol, 0) + qty
                else:
                    pending[o.symbol] = pending.get(o.symbol, 0) - qty
            return pending
        except Exception:
            return pending  # conservative: no pending
    # Legacy fallback: raw Alpaca client
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        for o in client.get_orders(filter=req):
            sym = o.symbol
            qty = float(o.qty) if o.qty else 0
            side_str = (o.side.value if hasattr(o.side, "value") else str(o.side)).lower()
            if side_str == "buy":
                pending[sym] = pending.get(sym, 0) + qty
            else:
                pending[sym] = pending.get(sym, 0) - qty
    except Exception:
        pass
    return pending


def reconcile(client, qty_tolerance: float = 0.001) -> dict:
    """v3.52.2: reconcile by SHARE QUANTITY, with pending-order awareness.

    Compares journal lots to Alpaca positions, but FIRST nets out any
    pending (open) orders. A lot whose corresponding BUY order is still
    open in Alpaca's queue is 'awaiting fill', not 'missing'.

    Quantities don't drift with price. Tolerance is fractional shares only
    (Alpaca rounds to ~4 decimal places, so 0.001 covers rounding).

    Returns:
      {
        matched / missing / unexpected / size_mismatch / awaiting_fill lists,
        halt_recommended: bool,
      }
    """
    expected = get_expected_positions_qty()
    actual = get_actual_positions_qty(client)
    pending = get_pending_orders_qty(client)

    matched, missing, unexpected, size_mismatch, awaiting_fill = [], [], [], [], []
    all_syms = set(expected) | set(actual)
    for sym in all_syms:
        e = expected.get(sym, 0)
        a = actual.get(sym, 0)
        p = pending.get(sym, 0)  # positive = pending BUY would add to position
        # Effective expected after netting pending fills.
        # If we have a journal lot but no Alpaca position AND there's a
        # pending buy of similar size, that's awaiting-fill, not missing.
        if e > 0 and a == 0:
            if p > 0 and abs(p - e) <= qty_tolerance:
                awaiting_fill.append({"symbol": sym, "expected_qty": e,
                                       "pending_qty": p,
                                       "reason": "pending BUY in Alpaca queue (likely after-hours order awaiting next open)"})
                continue
            missing.append({"symbol": sym, "expected_qty": e})
        elif e == 0 and a > 0:
            unexpected.append({"symbol": sym, "actual_qty": a})
        elif abs(e - a) > qty_tolerance:
            # Check if pending fills explain the gap
            effective_a = a + max(p, 0)  # add pending BUY qty
            if abs(e - effective_a) <= qty_tolerance:
                awaiting_fill.append({"symbol": sym, "expected_qty": e,
                                       "actual_qty": a, "pending_qty": p,
                                       "reason": "partial fill; remainder pending"})
                continue
            size_mismatch.append({"symbol": sym, "expected": e, "actual": a, "diff": a - e})
        else:
            matched.append({"symbol": sym, "qty": a})

    halt = bool(unexpected) or len(missing) > 1 or len(size_mismatch) > 2
    return {
        "matched": matched,
        "missing": missing,
        "unexpected": unexpected,
        "size_mismatch": size_mismatch,
        "awaiting_fill": awaiting_fill,  # NEW v3.52.2
        "halt_recommended": halt,
        "summary": (
            f"matched={len(matched)} missing={len(missing)} "
            f"unexpected={len(unexpected)} size_mismatch={len(size_mismatch)} "
            f"awaiting_fill={len(awaiting_fill)}"
        ),
    }
