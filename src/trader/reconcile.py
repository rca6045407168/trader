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


def reconcile(client, qty_tolerance: float = 0.001) -> dict:
    """v1.9: reconcile by SHARE QUANTITY, not dollar value.

    Quantities don't drift with price. Tolerance is fractional shares only
    (Alpaca rounds to ~4 decimal places, so 0.001 covers rounding).

    Returns:
      {
        matched / missing / unexpected / size_mismatch lists,
        halt_recommended: bool,
      }
    """
    expected = get_expected_positions_qty()
    actual = get_actual_positions_qty(client)

    matched, missing, unexpected, size_mismatch = [], [], [], []
    all_syms = set(expected) | set(actual)
    for sym in all_syms:
        e = expected.get(sym, 0)
        a = actual.get(sym, 0)
        if e == 0 and a > 0:
            unexpected.append({"symbol": sym, "actual_qty": a})
        elif e > 0 and a == 0:
            missing.append({"symbol": sym, "expected_qty": e})
        elif abs(e - a) > qty_tolerance:
            size_mismatch.append({"symbol": sym, "expected": e, "actual": a, "diff": a - e})
        else:
            matched.append({"symbol": sym, "qty": a})

    halt = bool(unexpected) or len(missing) > 1 or len(size_mismatch) > 2
    return {
        "matched": matched,
        "missing": missing,
        "unexpected": unexpected,
        "size_mismatch": size_mismatch,
        "halt_recommended": halt,
        "summary": (
            f"matched={len(matched)} missing={len(missing)} "
            f"unexpected={len(unexpected)} size_mismatch={len(size_mismatch)}"
        ),
    }
