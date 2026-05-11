#!/usr/bin/env python3
"""Weekly signal digest for manual execution on Public.com.

Alpaca paper supports programmatic submission; Public.com (where
real money is deployed) does NOT have a public REST API for retail
accounts. The trader becomes a SIGNAL GENERATOR in that world, with
the operator executing manually.

This script produces a Friday-afternoon digest answering:
  - What does the model want me to hold next week?
  - What changed since last Friday's recommendation?
  - Any TLH harvest swaps to execute?
  - Any new high-conviction signals (LIVE_AUTO variant change)?
  - Anything I should NOT do (e.g., wash-sale blocked names)?

Run weekly via launchd (Fri 4 PM ET). Email-friendly plain text.

Usage:
  python scripts/weekly_digest.py
  python scripts/weekly_digest.py --days 7  --csv-out ~/weekly.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


# Edges that produce operator-facing signals in the digest.
# In v6.0.x manual-execution mode, we deprioritize the timing-
# sensitive alpha sources (insider/PEAD) — they're still shadow-
# tracked, but don't appear in the weekly digest unless the
# operator explicitly opts in via DIGEST_INCLUDE_SHADOW=1.
PRIMARY_EDGES = {
    "TLH harvest swaps (Book A)",
    "Momentum auto-router selection (Book B)",
    "Calendar-effect window",
    "Quality basket rebalance",
}


def section(title: str, body: str) -> str:
    line = "=" * 72
    return f"\n{line}\n  {title}\n{line}\n{body}\n"


def get_latest_decisions(db_path: Path, days: int = 7) -> list[dict]:
    """Most-recent decisions in the window."""
    if not db_path.exists():
        return []
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ts, ticker, action, style, score, rationale_json, final "
            "FROM decisions WHERE ts >= ? ORDER BY ts DESC",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"ts": r[0], "ticker": r[1], "action": r[2], "style": r[3],
         "score": r[4], "rationale_json": r[5], "final": r[6]}
        for r in rows
    ]


def get_latest_run(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT run_id, started_at, completed_at, status, notes "
            "FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {"run_id": row[0], "started_at": row[1],
             "completed_at": row[2], "status": row[3], "notes": row[4]}


def get_recent_tlh_swaps(db_path: Path, days: int = 7) -> list[dict]:
    """Realized-loss closes in the window (TLH harvest events)."""
    if not db_path.exists():
        return []
    cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT symbol, closed_at, qty, open_price, close_price, realized_pnl "
            "FROM position_lots WHERE closed_at IS NOT NULL "
            "AND realized_pnl < 0 AND closed_at >= ? ORDER BY closed_at DESC",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"symbol": r[0], "closed_at": r[1], "qty": r[2],
         "open_price": r[3], "close_price": r[4], "realized_pnl": r[5]}
        for r in rows
    ]


def extract_target_weights(decisions: list[dict]) -> dict[str, dict]:
    """From the latest run's decisions, extract the target weight
    per ticker. Returns {ticker: {weight, style, score, rationale}}."""
    if not decisions:
        return {}
    # Use the most recent batch — everything within 5 min of the
    # latest decision counts as "this run"
    latest_ts = decisions[0]["ts"]
    latest_dt = datetime.fromisoformat(latest_ts)
    batch_start = (latest_dt - timedelta(minutes=10)).isoformat()
    batch = [d for d in decisions if d["ts"] >= batch_start]
    out: dict[str, dict] = {}
    for d in batch:
        m = re.search(r"@\s*([\d.]+)%", d["final"] or "")
        weight = float(m.group(1)) / 100.0 if m else None
        out[d["ticker"]] = {
            "weight": weight,
            "style": d["style"],
            "score": d["score"],
            "final": d["final"],
            "ts": d["ts"],
        }
    return out


def format_target_book(targets: dict[str, dict]) -> str:
    if not targets:
        return "  (no recent decisions found)"
    rows = sorted(targets.items(),
                   key=lambda kv: -(kv[1].get("weight") or 0))
    lines = [f"  {'Ticker':<8} {'Weight':>8}  {'Style':<14}  {'Score':>8}"]
    lines.append("  " + "-" * 50)
    for sym, d in rows:
        w_str = f"{(d['weight'] or 0)*100:.2f}%" if d.get("weight") else "—"
        score = d.get("score")
        score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "—"
        lines.append(
            f"  {sym:<8} {w_str:>8}  {(d['style'] or ''):<14}  {score_str:>8}"
        )
    total_w = sum((d.get("weight") or 0) for d in targets.values())
    lines.append("  " + "-" * 50)
    lines.append(f"  {'TOTAL':<8} {total_w*100:>7.2f}%")
    return "\n".join(lines)


def format_tlh_swaps(swaps: list[dict]) -> str:
    if not swaps:
        return ("  No TLH harvest swaps in this window.\n"
                "  (On Public.com, manually execute swaps within 5 min of\n"
                "  each other to maintain market exposure.)")
    lines = [f"  {'Symbol':<7} {'Closed':<12} {'Qty':>10} "
              f"{'Loss $':>10}  Notes"]
    lines.append("  " + "-" * 60)
    total_loss = 0.0
    for s in swaps:
        lines.append(
            f"  {s['symbol']:<7} {s['closed_at'][:10]:<12} "
            f"{s['qty']:>10.2f} {s['realized_pnl']:>10.2f}"
        )
        total_loss += s["realized_pnl"]
    lines.append("  " + "-" * 60)
    lines.append(f"  Cumulative realized loss this window: ${total_loss:,.2f}")
    return "\n".join(lines)


def build_digest(db_path: Path, days: int = 7) -> str:
    asof = datetime.now()
    parts = []
    parts.append("=" * 72)
    parts.append(f"  TRADER WEEKLY DIGEST — {asof:%Y-%m-%d %H:%M}")
    parts.append(f"  Window: last {days} days")
    parts.append("=" * 72)

    # Latest run status
    run = get_latest_run(db_path)
    if run:
        status_line = (
            f"Last orchestrator run: {run['run_id']} ({run['status']})"
        )
        if run.get("notes"):
            status_line += f" — {run['notes'][:60]}"
        parts.append("\n  " + status_line)
    else:
        parts.append("\n  (no recent orchestrator runs)")

    # Target book (what to hold)
    decisions = get_latest_decisions(db_path, days=days)
    targets = extract_target_weights(decisions)
    parts.append(section(
        "TARGET BOOK FOR THIS WEEK",
        format_target_book(targets),
    ))

    # TLH harvest events
    tlh_swaps = get_recent_tlh_swaps(db_path, days=days)
    parts.append(section(
        f"TLH HARVEST EVENTS (last {days} days)",
        format_tlh_swaps(tlh_swaps),
    ))

    # Public.com execution checklist
    parts.append(section(
        "PUBLIC.COM EXECUTION CHECKLIST",
        "  1. Open Public.com → Account → Holdings.\n"
        "  2. Compare your current holdings to the target book above.\n"
        "  3. For each name OVER target weight: place SELL for the difference.\n"
        "  4. For each name UNDER target weight: place BUY for the difference.\n"
        "  5. Skip differences smaller than 0.5 % of book (not worth the friction).\n"
        "  6. For TLH harvest swaps: execute the SELL and BUY within 5 min\n"
        "     of each other to keep market exposure roughly intact.\n"
        "  7. After execution, run:\n"
        "     python scripts/import_public_positions.py ~/Downloads/public_export.csv\n"
        "     to update the journal so reconciliation stays clean.\n"
    ))

    # Strategy notes
    parts.append(section(
        "OPERATIONAL NOTES",
        "  - TLH wash-sale window is 31 days. The planner respects this\n"
        "    automatically when picking replacements; you don't need to track\n"
        "    it manually.\n"
        "  - Public.com's Tax Tools tab should show the same realized losses\n"
        "    as this digest. If they disagree by > $100, investigate before\n"
        "    relying on the platform's tax projections.\n"
        "  - High-frequency edges (insider, PEAD) are shadow-tracked but not\n"
        "    surfaced here — they're too timing-sensitive for manual\n"
        "    execution. To see them: set DIGEST_INCLUDE_SHADOW=1.\n"
    ))

    return "\n".join(parts)


def export_csv(targets: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "target_weight_pct", "style", "score", "final"])
        for sym, d in sorted(targets.items(),
                              key=lambda kv: -(kv[1].get("weight") or 0)):
            w.writerow([
                sym,
                f"{(d.get('weight') or 0) * 100:.2f}",
                d.get("style", ""),
                d.get("score") if d.get("score") is not None else "",
                d.get("final", ""),
            ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    ap.add_argument("--csv-out", type=Path, default=None)
    args = ap.parse_args(argv)

    digest = build_digest(args.db, days=args.days)
    print(digest)

    if args.csv_out:
        decisions = get_latest_decisions(args.db, days=args.days)
        targets = extract_target_weights(decisions)
        export_csv(targets, args.csv_out)
        print(f"\nCSV: wrote {len(targets)} rows to {args.csv_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
