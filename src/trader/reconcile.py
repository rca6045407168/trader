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


def get_expected_positions() -> dict[str, float]:
    """Reconstruct expected positions from journal: latest daily_snapshot OR sum of orders."""
    snaps = recent_snapshots(days=2)
    if snaps:
        return json.loads(snaps[0]["positions_json"])
    return {}


def get_actual_positions(client) -> dict[str, float]:
    """Pull current Alpaca positions."""
    return {p.symbol: float(p.market_value) for p in client.get_all_positions()}


def reconcile(client, tolerance_usd: float = 50.0) -> dict:
    """Compare expected vs actual; return diff report.

    Returns:
      {
        "matched": [...],
        "missing": [...],     # in expected, not actual (closed by stop, or order failed)
        "unexpected": [...],  # in actual, not expected (someone clicked? bug?)
        "size_mismatch": [...],  # both sides but >tolerance_usd different
        "halt_recommended": bool,
      }
    """
    expected = get_expected_positions()
    actual = get_actual_positions(client)

    matched, missing, unexpected, size_mismatch = [], [], [], []
    all_syms = set(expected) | set(actual)
    for sym in all_syms:
        e = expected.get(sym, 0)
        a = actual.get(sym, 0)
        if e == 0 and a > 0:
            unexpected.append({"symbol": sym, "actual_value": a})
        elif e > 0 and a == 0:
            missing.append({"symbol": sym, "expected_value": e})
        elif abs(e - a) > tolerance_usd:
            size_mismatch.append({"symbol": sym, "expected": e, "actual": a, "diff": a - e})
        else:
            matched.append({"symbol": sym, "value": a})

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
