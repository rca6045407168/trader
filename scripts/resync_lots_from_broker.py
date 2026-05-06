#!/usr/bin/env python3
"""v3.73.16 — Resync position_lots from Alpaca (broker is ground truth).

Use case: the journal's `position_lots` table has drifted from
Alpaca's actual positions (e.g., a fill was double-recorded, a
manual broker action occurred, or a previous run mis-journaled
fractional shares). The reconcile gate then refuses to let the
orchestrator place new orders — correctly — until the drift is
resolved.

This script resolves it by:
  1. Snapshotting Alpaca's current per-symbol position quantities
  2. Closing all OPEN lots in journal.position_lots (closed_at = now,
     close_price = current Alpaca last price, with a 'broker-resync'
     reason marker)
  3. Inserting one new OPEN lot per Alpaca position with qty matching
     the broker exactly
  4. Running reconcile() to verify the fix

By design this is irreversible-but-recoverable: the closed lots stay
in the journal as historical record. The new lots become the new
truth going forward.

Usage:
  python scripts/resync_lots_from_broker.py --check    # dry run, show diff
  python scripts/resync_lots_from_broker.py --apply    # do it
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.execute import get_client, get_last_price  # noqa: E402
from trader.journal import _conn, init_db  # noqa: E402
from trader.reconcile import reconcile  # noqa: E402


def snapshot_broker_positions() -> dict:
    """Pull (symbol → qty) from Alpaca right now."""
    client = get_client()
    out = {}
    for p in client.get_all_positions():
        out[p.symbol] = float(p.qty)
    return out


def fetch_open_lots() -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            """SELECT id, symbol, sleeve, opened_at, qty, open_price
               FROM position_lots WHERE closed_at IS NULL"""
        ).fetchall()
    return [
        dict(id=r[0], symbol=r[1], sleeve=r[2], opened_at=r[3],
             qty=r[4], open_price=r[5])
        for r in rows
    ]


def apply_resync(broker: dict, dry: bool = True) -> int:
    """Close all open lots; reopen one per broker position. Returns
    the number of lots written (closed + opened)."""
    init_db()
    open_lots = fetch_open_lots()
    now = datetime.utcnow().isoformat()

    print(f"\nClosing {len(open_lots)} open lots, opening {len(broker)} fresh lots from broker.")
    if dry:
        print("(dry run — no DB writes)")
        return 0

    n_writes = 0
    with _conn() as c:
        # 1. Close all open lots
        for lot in open_lots:
            close_px = get_last_price(lot["symbol"]) or lot["open_price"]
            c.execute(
                """UPDATE position_lots SET closed_at=?, close_price=?
                   WHERE id=?""",
                (now, float(close_px), lot["id"]),
            )
            n_writes += 1

        # 2. Open new lots from broker truth
        for sym, qty in broker.items():
            px = get_last_price(sym) or 0.0
            c.execute(
                """INSERT INTO position_lots
                   (symbol, sleeve, opened_at, qty, open_price, open_order_id)
                   VALUES (?, 'broker-resync', ?, ?, ?, NULL)""",
                (sym, now, float(qty), float(px)),
            )
            n_writes += 1

    return n_writes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                     help="Actually write to journal (default is dry-run)")
    ap.add_argument("--check", action="store_true",
                     help="Dry-run + show what would change")
    args = ap.parse_args()
    dry = not args.apply

    print("=" * 60)
    print("LOT RESYNC FROM BROKER")
    print("=" * 60)

    broker = snapshot_broker_positions()
    print(f"\nBroker has {len(broker)} positions:")
    for sym, qty in sorted(broker.items()):
        print(f"  {sym:6s} {qty:>10.4f}")

    open_lots = fetch_open_lots()
    print(f"\nJournal has {len(open_lots)} open lots:")
    for lot in sorted(open_lots, key=lambda x: x["symbol"]):
        bq = broker.get(lot["symbol"], 0.0)
        flag = "" if abs(lot["qty"] - bq) < 0.001 else "  ← DRIFT"
        print(f"  {lot['symbol']:6s} jrnl={lot['qty']:>10.4f}  broker={bq:>10.4f}{flag}")

    n = apply_resync(broker, dry=dry)

    if not dry:
        print(f"\nWrote {n} rows. Re-running reconcile to verify...")
        rep = reconcile(get_client())
        print(f"\nPost-resync reconcile: {rep['summary']}")
        print(f"halt_recommended: {rep['halt_recommended']}")
        if rep['halt_recommended']:
            print("STILL DRIFTING — investigate manually.")
            return 1
        print("✅ Journal now matches broker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
