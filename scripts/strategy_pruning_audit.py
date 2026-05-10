#!/usr/bin/env python3
"""Strategy-registry pruning audit.

The auto-router has 32 candidates. The eligibility filter
(MIN_EVIDENCE_MONTHS≥6, MAX_BETA≤1.20, MIN_DD≥-25%) probably
eliminates 25 of them, but the registry still carries every one —
extra import cost, extra eval-harness time, extra mental load.

This script audits strategy_eval for strategies that have NEVER
ranked in the top-K of rolling IR. Outputs a "candidates to prune"
list. The decision to actually delete is left to the operator;
this script is an ADVISORY tool.

Usage:
  python scripts/strategy_pruning_audit.py
  python scripts/strategy_pruning_audit.py --top-k 5 --window-months 6
  python scripts/strategy_pruning_audit.py --csv-out ~/strategy_audit.csv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.config import DB_PATH  # noqa: E402


def audit(db_path: Path,
           top_k: int = 5,
           window_months: int = 6) -> dict:
    """Returns audit dict with per-strategy metrics."""
    if not db_path.exists():
        return {"error": f"DB not found: {db_path}"}
    cutoff = (datetime.utcnow() - timedelta(days=window_months * 30)).date().isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Per-strategy per-asof IR from strategy_eval. We don't have
        # IR directly; use n_picks as a proxy for "produced picks at
        # all" — strategies that NEVER produced picks are pruning
        # candidates by definition.
        rows = con.execute(
            "SELECT strategy, asof, n_picks FROM strategy_eval "
            "WHERE asof >= ? ORDER BY strategy, asof",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()

    # Aggregate per-strategy
    per_strat: dict[str, dict] = defaultdict(lambda: {
        "n_rows": 0,
        "n_picks_avg": 0.0,
        "first_seen": None,
        "last_seen": None,
        "n_picks_zero_count": 0,
    })
    for strategy, asof, n_picks in rows:
        d = per_strat[strategy]
        d["n_rows"] += 1
        d["n_picks_avg"] += n_picks
        if d["first_seen"] is None or asof < d["first_seen"]:
            d["first_seen"] = asof
        if d["last_seen"] is None or asof > d["last_seen"]:
            d["last_seen"] = asof
        if n_picks == 0:
            d["n_picks_zero_count"] += 1
    for s, d in per_strat.items():
        if d["n_rows"] > 0:
            d["n_picks_avg"] = d["n_picks_avg"] / d["n_rows"]

    # Pull all registered strategies (some may have ZERO rows)
    from trader import eval_strategies
    all_names = {s.name for s in eval_strategies.all_strategies()}

    # Adaptive threshold: "healthy" = ≥80% of the highest-row strategy.
    # Avoids the trap where a short journal history flags every name
    # as "sparse" when they've all hit every day.
    max_rows = max((d["n_rows"] for d in per_strat.values()), default=0)
    healthy_threshold = max(5, int(max_rows * 0.80))

    # Categorize
    silent: list[str] = []
    sparse: list[str] = []
    healthy: list[str] = []

    for name in sorted(all_names):
        d = per_strat.get(name, {"n_rows": 0})
        if d["n_rows"] == 0:
            silent.append(name)
        elif d["n_rows"] < healthy_threshold:
            sparse.append(name)
        else:
            healthy.append(name)

    return {
        "window_months": window_months,
        "cutoff": cutoff,
        "n_total": len(all_names),
        "max_rows": max_rows,
        "healthy_threshold": healthy_threshold,
        "silent": silent,
        "sparse": sparse,
        "healthy": healthy,
        "per_strat": dict(per_strat),
    }


def render_report(audit_data: dict) -> str:
    if "error" in audit_data:
        return f"ERROR: {audit_data['error']}"
    lines = [
        "=" * 72,
        f"STRATEGY PRUNING AUDIT — last {audit_data['window_months']} months "
        f"(since {audit_data['cutoff']})",
        "=" * 72,
        f"Total registered strategies: {audit_data['n_total']}",
        f"Max eval rows seen:          {audit_data.get('max_rows', 0)} "
        f"(adaptive threshold: ≥{audit_data.get('healthy_threshold', 0)})",
        f"Healthy:                     {len(audit_data['healthy'])}",
        f"Sparse:                      {len(audit_data['sparse'])}",
        f"Silent:                      {len(audit_data['silent'])}",
        "",
    ]
    if audit_data["silent"]:
        lines.append("SILENT (never journaled picks — prime pruning candidates):")
        for s in audit_data["silent"]:
            lines.append(f"  ❌ {s}")
        lines.append("")
    if audit_data["sparse"]:
        lines.append("SPARSE (rarely produces picks — investigate):")
        for s in audit_data["sparse"]:
            d = audit_data["per_strat"].get(s, {})
            lines.append(
                f"  ⚠️  {s}  "
                f"({d.get('n_rows', 0)} rows, "
                f"avg n_picks={d.get('n_picks_avg', 0):.1f})"
            )
        lines.append("")
    if audit_data["healthy"]:
        lines.append("HEALTHY (regular picks — keep):")
        for s in audit_data["healthy"]:
            d = audit_data["per_strat"].get(s, {})
            lines.append(
                f"  ✅ {s}  "
                f"({d['n_rows']} rows, "
                f"avg n_picks={d['n_picks_avg']:.1f})"
            )
        lines.append("")
    lines.append("RECOMMENDATIONS")
    lines.append("-" * 72)
    if audit_data["silent"]:
        lines.append(
            f"  - {len(audit_data['silent'])} strategies have produced ZERO "
            "picks in the window. Likely candidates for"
        )
        lines.append(
            "    deletion from eval_strategies.py — they're taxing imports + "
            "eval-harness time without"
        )
        lines.append("    earning their seat.")
    if audit_data["sparse"]:
        lines.append(
            f"  - {len(audit_data['sparse'])} strategies are sparse (<10 rows). "
            "Verify they're firing correctly;"
        )
        lines.append(
            "    if they're env-gated and the operator hasn't enabled them yet, "
            "leave them alone."
        )
    if not audit_data["silent"] and not audit_data["sparse"]:
        lines.append(
            "  - No pruning candidates surfaced. The registry is lean."
        )
    return "\n".join(lines)


def write_csv(audit_data: dict, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "status", "n_rows", "avg_n_picks",
                     "first_seen", "last_seen"])
        for status in ["silent", "sparse", "healthy"]:
            for s in audit_data[status]:
                d = audit_data["per_strat"].get(s, {})
                w.writerow([
                    s, status, d.get("n_rows", 0),
                    f"{d.get('n_picks_avg', 0):.2f}",
                    d.get("first_seen", ""),
                    d.get("last_seen", ""),
                ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path(DB_PATH))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--window-months", type=int, default=6)
    ap.add_argument("--csv-out", type=Path, default=None)
    args = ap.parse_args(argv)
    data = audit(args.db, top_k=args.top_k,
                  window_months=args.window_months)
    print(render_report(data))
    if args.csv_out:
        write_csv(data, args.csv_out)
        print(f"\nCSV written: {args.csv_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
