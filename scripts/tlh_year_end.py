#!/usr/bin/env python3
"""TLH year-end report — what to hand the accountant on Apr 15.

Pulls realized losses from `position_lots` (closed_at NOT NULL,
realized_pnl < 0) for a given tax year and produces:

  1. Per-ticker summary (count of harvest closes, total realized loss)
  2. Aggregate: YTD realized loss + projected tax savings at the
     supplied marginal rates
  3. Wash-sale recapture caveat: tickers re-bought within 31 days of
     a loss-close. The actual disallowed amount is computed on the
     1099-B by the broker; we just FLAG positions for the accountant
     to double-check.
  4. Carry-forward estimate: anything over $3k offsets capital gains
     (no limit). If you have no capital gains this year, $3k offsets
     ordinary income and the rest carries forward indefinitely.

Usage:
    python scripts/tlh_year_end.py
    python scripts/tlh_year_end.py --year 2026 --tax-rate 0.32 --state-rate 0.05
    python scripts/tlh_year_end.py --csv-out ~/tlh_2026.csv

Output is human-readable to stdout. With --csv-out, also writes a
per-close-event CSV the accountant can import directly. The CSV
columns mirror the 1099-B form columns so reconciliation is trivial.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Allow running from repo root or scripts/ directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trader.config import DB_PATH  # noqa: E402
from trader.direct_index_tlh import WASH_SALE_DAYS  # noqa: E402


@dataclass
class CloseEvent:
    """One realized loss event (a closed lot)."""
    symbol: str
    sleeve: str
    opened_at: str
    closed_at: str
    qty: float
    open_price: float
    close_price: float
    realized_pnl: float
    holding_period_days: int

    @property
    def is_long_term(self) -> bool:
        """LTCG treatment requires > 365 days held."""
        return self.holding_period_days > 365


@dataclass
class WashSaleFlag:
    """A closed-at-loss event followed by a buy of the same symbol
    within 31 days. We flag for the accountant; actual disallowance
    is per-tax-lot on the 1099-B."""
    symbol: str
    loss_closed_at: str
    loss_amount: float
    repurchase_at: str
    days_between: int


def _q(db_path: str | Path, sql: str, params: tuple = ()) -> list[tuple]:
    p = Path(db_path)
    if not p.exists():
        return []
    try:
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
            return c.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise


def fetch_loss_closes(db_path: str | Path, year: int) -> list[CloseEvent]:
    """All closed lots with realized_pnl < 0 inside `year`.

    Holding period is measured between opened_at and closed_at; for
    partial-close lots, opened_at on the synthetic sub-lot is copied
    from the parent (per journal.close_lots_fifo)."""
    start = f"{year}-01-01"
    end = f"{year + 1}-01-01"
    rows = _q(
        db_path,
        "SELECT symbol, sleeve, opened_at, closed_at, qty, "
        "open_price, close_price, realized_pnl FROM position_lots "
        "WHERE closed_at IS NOT NULL "
        "AND closed_at >= ? AND closed_at < ? "
        "AND realized_pnl IS NOT NULL AND realized_pnl < 0 "
        "ORDER BY closed_at ASC",
        (start, end),
    )
    out: list[CloseEvent] = []
    for symbol, sleeve, opened_at, closed_at, qty, open_px, close_px, pnl in rows:
        try:
            opened_dt = datetime.fromisoformat(opened_at).date()
            closed_dt = datetime.fromisoformat(closed_at).date()
            days = (closed_dt - opened_dt).days
        except Exception:
            days = 0
        out.append(CloseEvent(
            symbol=symbol,
            sleeve=sleeve or "",
            opened_at=opened_at,
            closed_at=closed_at,
            qty=float(qty or 0),
            open_price=float(open_px or 0),
            close_price=float(close_px or 0),
            realized_pnl=float(pnl or 0),
            holding_period_days=days,
        ))
    return out


def find_wash_sale_flags(db_path: str | Path,
                          closes: list[CloseEvent]) -> list[WashSaleFlag]:
    """For each loss-close, scan position_lots for a buy of the same
    symbol within WASH_SALE_DAYS after (or before) the close date.

    The IRS wash-sale window is 30 days BEFORE through 30 days AFTER
    the loss-realizing sale. We check both directions."""
    flags: list[WashSaleFlag] = []
    for ev in closes:
        try:
            closed_dt = datetime.fromisoformat(ev.closed_at).date()
        except Exception:
            continue
        window_start = (closed_dt - timedelta(days=WASH_SALE_DAYS)).isoformat()
        window_end = (closed_dt + timedelta(days=WASH_SALE_DAYS)).isoformat()
        rows = _q(
            db_path,
            "SELECT opened_at FROM position_lots "
            "WHERE symbol = ? AND opened_at >= ? AND opened_at <= ? "
            "ORDER BY opened_at ASC",
            (ev.symbol, window_start, window_end),
        )
        for (opened_at,) in rows:
            try:
                opened_dt = datetime.fromisoformat(opened_at).date()
            except Exception:
                continue
            if opened_dt == closed_dt:
                continue  # ignore the close event itself
            days_between = abs((opened_dt - closed_dt).days)
            if 0 < days_between <= WASH_SALE_DAYS:
                flags.append(WashSaleFlag(
                    symbol=ev.symbol,
                    loss_closed_at=ev.closed_at,
                    loss_amount=ev.realized_pnl,
                    repurchase_at=opened_at,
                    days_between=days_between,
                ))
                break  # one flag per loss event is enough
    return flags


def estimate_tax_savings(total_loss: float,
                          federal_rate: float,
                          state_rate: float,
                          capital_gains_offset: float = 0.0) -> dict:
    """Estimate $ saved given total realized loss and offsetting gains.

    Rules:
      - Losses first offset realized capital gains (any amount).
      - Remaining loss offsets ordinary income up to $3,000/yr.
      - Anything beyond that carries forward indefinitely.

    Returns a dict of components so the operator can see exactly how
    the numbers were derived (no black box)."""
    # total_loss is a negative number; convert to positive for math
    loss_abs = abs(total_loss)
    cg_offset = min(loss_abs, capital_gains_offset)
    remaining_after_cg = loss_abs - cg_offset
    ordinary_offset = min(remaining_after_cg, 3000.0)
    carry_forward = remaining_after_cg - ordinary_offset
    combined_rate = federal_rate + state_rate
    # Tax saved on the capital-gains offset uses the same rate (assumes
    # ST gains; for LT gains the rate is lower — operator can override
    # via capital_gains_offset=0 and compute manually if needed).
    cg_savings = cg_offset * combined_rate
    ord_savings = ordinary_offset * combined_rate
    return {
        "loss_abs": loss_abs,
        "cg_offset": cg_offset,
        "ordinary_offset": ordinary_offset,
        "carry_forward": carry_forward,
        "cg_savings": cg_savings,
        "ordinary_savings": ord_savings,
        "total_savings": cg_savings + ord_savings,
        "combined_rate": combined_rate,
    }


def aggregate_by_symbol(closes: list[CloseEvent]) -> list[dict]:
    """Per-ticker rollup. Sorted by total realized loss (most-negative
    first) so the biggest contributors top the report."""
    agg: dict[str, dict] = {}
    for ev in closes:
        d = agg.setdefault(ev.symbol, {
            "symbol": ev.symbol,
            "count": 0,
            "total_loss": 0.0,
            "qty_total": 0.0,
            "lt_count": 0,
            "st_count": 0,
            "sleeves": set(),
        })
        d["count"] += 1
        d["total_loss"] += ev.realized_pnl
        d["qty_total"] += ev.qty
        if ev.is_long_term:
            d["lt_count"] += 1
        else:
            d["st_count"] += 1
        if ev.sleeve:
            d["sleeves"].add(ev.sleeve)
    rows = []
    for sym in agg:
        d = agg[sym]
        d["sleeves"] = ",".join(sorted(d["sleeves"]))
        rows.append(d)
    rows.sort(key=lambda r: r["total_loss"])  # most negative first
    return rows


def render_report(closes: list[CloseEvent],
                  flags: list[WashSaleFlag],
                  year: int,
                  federal_rate: float,
                  state_rate: float,
                  capital_gains_offset: float = 0.0) -> str:
    """Produce a plain-text report suitable for stdout or email."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"TLH YEAR-END REPORT — TAX YEAR {year}")
    lines.append("=" * 72)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Source: {DB_PATH}")
    lines.append("")

    if not closes:
        lines.append("No loss-realizing closes recorded for this year.")
        lines.append("")
        lines.append("Reasons this might be empty:")
        lines.append("  - TLH_ENABLED was false during the year (default)")
        lines.append("  - Paper trading account (no real fills)")
        lines.append("  - First year of operation, positions still developing")
        lines.append("  - Strong year — no positions reached the 5% loss threshold")
        return "\n".join(lines)

    total_loss = sum(ev.realized_pnl for ev in closes)
    lt_loss = sum(ev.realized_pnl for ev in closes if ev.is_long_term)
    st_loss = total_loss - lt_loss

    # --- Headline ---
    lines.append("HEADLINE")
    lines.append("-" * 72)
    lines.append(f"  Closes harvested:       {len(closes):>10}")
    lines.append(f"  Total realized loss:    ${total_loss:>14,.2f}")
    lines.append(f"    Short-term (<=1yr):   ${st_loss:>14,.2f}")
    lines.append(f"    Long-term  (>1yr):    ${lt_loss:>14,.2f}")
    lines.append("")

    # --- Tax savings ---
    est = estimate_tax_savings(
        total_loss=total_loss,
        federal_rate=federal_rate,
        state_rate=state_rate,
        capital_gains_offset=capital_gains_offset,
    )
    lines.append("ESTIMATED TAX SAVINGS")
    lines.append("-" * 72)
    lines.append(f"  Marginal rates:         "
                  f"federal {federal_rate:.0%} + state {state_rate:.0%} "
                  f"= {est['combined_rate']:.0%}")
    lines.append(f"  Capital-gains offset:   ${est['cg_offset']:>14,.2f}"
                  f"  (assumed CG of ${capital_gains_offset:,.0f})")
    lines.append(f"  Ordinary-income offset: ${est['ordinary_offset']:>14,.2f}"
                  f"  (max $3,000/yr by IRS rule)")
    lines.append(f"  Carry-forward:          ${est['carry_forward']:>14,.2f}"
                  f"  (offsets future years, no expiry)")
    lines.append(f"  ★ Estimated $ saved THIS year: "
                  f"${est['total_savings']:>10,.2f}")
    lines.append("")
    lines.append("  Caveat: this is an estimate. The actual amount depends on")
    lines.append("  your full Schedule D (other gains/losses across all accounts),")
    lines.append("  AMT, NIIT, and any wash-sale recapture (see below).")
    lines.append("")

    # --- Per-ticker rollup ---
    lines.append("PER-TICKER ROLLUP")
    lines.append("-" * 72)
    lines.append(f"  {'Symbol':<8} {'Closes':>6} {'Loss $':>14} "
                  f"{'ST':>4} {'LT':>4}  Sleeves")
    for r in aggregate_by_symbol(closes):
        lines.append(f"  {r['symbol']:<8} {r['count']:>6} "
                      f"${r['total_loss']:>13,.2f} "
                      f"{r['st_count']:>4} {r['lt_count']:>4}  {r['sleeves']}")
    lines.append("")

    # --- Wash-sale flags ---
    lines.append("WASH-SALE FLAGS")
    lines.append("-" * 72)
    if not flags:
        lines.append("  None detected within the 31-day window. (The system")
        lines.append("  intentionally avoids wash sales — replacements are")
        lines.append("  sector-matched, not substantially identical.)")
    else:
        lines.append(f"  {len(flags)} potential wash-sale event(s) — ACCOUNTANT")
        lines.append("  TO REVIEW. Broker 1099-B is authoritative.")
        lines.append("")
        lines.append(f"  {'Symbol':<8} {'Loss closed':<20} {'Re-bought':<20} "
                      f"{'Days':>4} {'$ at risk':>14}")
        for f in flags:
            lines.append(f"  {f.symbol:<8} {f.loss_closed_at[:19]:<20} "
                          f"{f.repurchase_at[:19]:<20} "
                          f"{f.days_between:>4} ${f.loss_amount:>13,.2f}")
    lines.append("")

    # --- What to do at tax time ---
    lines.append("WHAT TO DO AT TAX TIME")
    lines.append("-" * 72)
    lines.append("  1. Pull broker 1099-B (Alpaca → Tax Documents). It will")
    lines.append("     list every covered close. Reconcile against this report.")
    lines.append("  2. Discrepancies > $50: investigate. Common causes are")
    lines.append("     partial fills, broker rounding, or wash-sale recapture")
    lines.append("     the system didn't flag.")
    lines.append("  3. Hand the 1099-B + this report to your accountant. They'll")
    lines.append("     file Schedule D (capital gains) + Form 8949 (per-lot).")
    lines.append("  4. Carry-forward loss (${:,.2f}) goes on next year's"
                  .format(est["carry_forward"]))
    lines.append("     return. The system will pick up automatically; no action")
    lines.append("     needed on your side.")
    lines.append("")
    return "\n".join(lines)


def write_csv(closes: list[CloseEvent], csv_path: Path) -> int:
    """Write a per-event CSV for accountant import. Columns mirror
    1099-B / Form 8949 layout."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol",
            "sleeve",
            "date_acquired",
            "date_sold",
            "qty",
            "proceeds",
            "cost_basis",
            "realized_loss",
            "holding_period_days",
            "term",
        ])
        for ev in closes:
            w.writerow([
                ev.symbol,
                ev.sleeve,
                ev.opened_at[:10],
                ev.closed_at[:10],
                f"{ev.qty:.6f}",
                f"{ev.qty * ev.close_price:.2f}",
                f"{ev.qty * ev.open_price:.2f}",
                f"{ev.realized_pnl:.2f}",
                ev.holding_period_days,
                "LT" if ev.is_long_term else "ST",
            ])
    return len(closes)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TLH year-end report")
    ap.add_argument("--year", type=int, default=date.today().year,
                     help="Tax year (default: current year)")
    ap.add_argument("--tax-rate", type=float, default=0.32,
                     help="Federal marginal rate (default 0.32 = 32%%)")
    ap.add_argument("--state-rate", type=float, default=0.05,
                     help="State marginal rate (default 0.05 = 5%%)")
    ap.add_argument("--capital-gains", type=float, default=0.0,
                     help="Realized capital gains this year ($), if any. "
                          "Losses offset these first.")
    ap.add_argument("--csv-out", type=Path, default=None,
                     help="Also write per-event CSV to this path")
    ap.add_argument("--db", type=Path, default=Path(DB_PATH),
                     help="SQLite journal path (default: trader DB_PATH)")
    args = ap.parse_args(argv)

    closes = fetch_loss_closes(args.db, args.year)
    flags = find_wash_sale_flags(args.db, closes)

    report = render_report(
        closes=closes,
        flags=flags,
        year=args.year,
        federal_rate=args.tax_rate,
        state_rate=args.state_rate,
        capital_gains_offset=args.capital_gains,
    )
    print(report)

    if args.csv_out:
        n = write_csv(closes, args.csv_out)
        print(f"\nCSV: wrote {n} event(s) to {args.csv_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
