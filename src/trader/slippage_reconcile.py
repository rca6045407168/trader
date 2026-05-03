"""[v3.59.0] Close the slippage_log loop.

execute.place_target_weights writes a slippage_log row at submit time
with decision_mid + notional but no fill_price (the order is just
queued at that moment). This module fills in fill_price + slippage_bps
by querying Alpaca for each open order's filled_avg_price and updating
the row in-place.

Run:
  python scripts/reconcile_slippage.py

Or call reconcile_slippage_log() from the daily cron path after orders
have had a chance to fill.

Idempotent: rows that already have a fill_price are skipped. Rows whose
order is still pending (Alpaca returns None for filled_avg) are left
untouched and retried on the next call.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from .config import DATA_DIR


DB_PATH = DATA_DIR / "journal.db"


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add fill_price + slippage_bps columns if they don't exist yet.
    The CREATE-TABLE-IF-NOT-EXISTS in execute.py only ran once; later
    schema additions need ALTER TABLE."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(slippage_log)")}
    if "fill_price" not in cols:
        try:
            conn.execute("ALTER TABLE slippage_log ADD COLUMN fill_price REAL")
        except Exception:
            pass
    if "slippage_bps" not in cols:
        try:
            conn.execute("ALTER TABLE slippage_log ADD COLUMN slippage_bps REAL")
        except Exception:
            pass
    if "alpaca_order_id" not in cols:
        try:
            conn.execute("ALTER TABLE slippage_log ADD COLUMN alpaca_order_id TEXT")
        except Exception:
            pass


def _slip_bps(side: str, decision_mid: float, fill_price: float) -> float:
    if decision_mid <= 0:
        return 0.0
    if side.lower() in ("buy", "b"):
        return (fill_price - decision_mid) / decision_mid * 1e4
    return (decision_mid - fill_price) / decision_mid * 1e4


def reconcile_slippage_log(max_rows: int = 500) -> dict:
    """Walk recent rows in slippage_log, fill in fill_price + slippage_bps
    by matching against journal.orders → Alpaca filled_avg_price.

    Returns {checked, updated, still_pending, errors}.
    """
    out = {"checked": 0, "updated": 0, "still_pending": 0, "errors": 0}
    if not DB_PATH.exists():
        return out
    try:
        from .execute import get_client
        client = get_client()
    except Exception as e:
        out["errors"] += 1
        out["error_msg"] = f"{type(e).__name__}: {e}"
        return out

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_columns(conn)
        # Pull pending rows: those without fill_price set
        cur = conn.execute(
            "SELECT rowid, ts, symbol, side, decision_mid, notional "
            "FROM slippage_log WHERE fill_price IS NULL "
            "ORDER BY ts DESC LIMIT ?",
            (max_rows,),
        )
        rows = cur.fetchall()

    for rowid, ts, symbol, side, decision_mid, notional in rows:
        out["checked"] += 1
        # Match to journal.orders by ts ± 5min and symbol+side
        try:
            with sqlite3.connect(DB_PATH) as conn:
                order_row = conn.execute(
                    "SELECT alpaca_order_id FROM orders "
                    "WHERE ticker = ? AND side = ? "
                    "AND ABS(julianday(ts) - julianday(?)) * 86400 < 600 "
                    "ORDER BY ts DESC LIMIT 1",
                    (symbol, side, ts),
                ).fetchone()
        except Exception:
            order_row = None

        if not order_row or not order_row[0]:
            out["still_pending"] += 1
            continue

        order_id = order_row[0]
        try:
            order = client.get_order_by_id(order_id)
            filled_avg = float(getattr(order, "filled_avg_price", 0) or 0)
        except Exception:
            out["errors"] += 1
            continue

        if not filled_avg or filled_avg <= 0:
            out["still_pending"] += 1
            continue

        # Compute and write back
        bps = _slip_bps(side or "buy", decision_mid or 0, filled_avg)
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE slippage_log SET fill_price = ?, "
                    "slippage_bps = ?, alpaca_order_id = ? WHERE rowid = ?",
                    (filled_avg, bps, order_id, rowid),
                )
            out["updated"] += 1
        except Exception:
            out["errors"] += 1

    return out


if __name__ == "__main__":
    print(reconcile_slippage_log())
