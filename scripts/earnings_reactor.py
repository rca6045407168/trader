"""Run the earnings reactor for current LIVE positions (v3.68.0).

Usage:
    python scripts/earnings_reactor.py                  # all live positions
    python scripts/earnings_reactor.py --symbol NVDA    # one symbol
    python scripts/earnings_reactor.py --since-days 30  # wider lookback
    python scripts/earnings_reactor.py --skip-claude    # archive only, no LLM

Reads current Alpaca positions to know which symbols matter, fetches
recent SEC 8-Ks for each, archives + analyzes new material filings,
writes structured signals into journal.earnings_signals.

Idempotent at every layer: re-running on the same day is safe — already
archived filings are skipped, already analyzed signals are skipped.

Best-effort: errors on one symbol don't stop the others. The exit code
is 0 unless something catastrophic happens.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _live_positions() -> list[str]:
    """Read current LIVE position tickers from Alpaca. Falls back to
    a hard-coded shortlist if the broker is unreachable."""
    try:
        from trader.positions_live import fetch_live_portfolio
        pf = fetch_live_portfolio()
        if pf.error or not pf.positions:
            print(f"  ! broker unreachable ({pf.error}); using fallback list")
            return ["AAPL", "AMD", "AVGO", "CAT", "GOOGL", "INTC",
                    "NVDA", "TSLA"]
        return [p.symbol for p in pf.positions if float(p.qty) > 0]
    except Exception as e:
        print(f"  ! could not read live portfolio: {e}")
        return ["AAPL", "AMD", "AVGO", "CAT", "GOOGL", "INTC",
                "NVDA", "TSLA"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--symbol", help="Single symbol to react on; default = all live positions")
    parser.add_argument("--since-days", type=int, default=14,
                         help="Lookback window in days (default 14)")
    parser.add_argument("--all-8k", action="store_true",
                         help="Process all 8-Ks (default: only material items)")
    parser.add_argument("--skip-claude", action="store_true",
                         help="Archive new filings but skip Claude analysis")
    parser.add_argument("--model", default=os.getenv("EARNINGS_REACTOR_MODEL",
                                                       "claude-sonnet-4-6"))
    parser.add_argument("--no-alerts", action="store_true",
                         help="Skip the email-alert layer (default: alert when "
                              "materiality ≥ REACTOR_ALERT_MIN_MATERIALITY)")
    parser.add_argument("--backfill-alerts", action="store_true",
                         help="Send alerts for any material signals already in "
                              "the journal that haven't been notified yet. "
                              "Doesn't fetch new filings.")
    args = parser.parse_args()

    if args.backfill_alerts:
        from trader.earnings_reactor import alert_unsent_signals
        sent = alert_unsent_signals(since_days=args.since_days)
        if sent:
            print(f"=== backfill done · sent {len(sent)} alert(s) ===")
            for sym, acc in sent:
                print(f"  ✓ {sym:6s} {acc}")
        else:
            print("=== backfill done · no unsent material signals ===")
        return 0

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = _live_positions()

    if not symbols:
        print("no symbols to react on")
        return 0

    print(f"=== earnings reactor ({len(symbols)} symbols, "
           f"since={args.since_days}d, model={args.model}) ===")

    if args.skip_claude:
        # Archive-only path: store new filings without LLM
        from trader import sec_filings, filings_archive
        n_new = 0
        for sym in symbols:
            t0 = time.time()
            metas = sec_filings.fetch_recent_filings(
                sym, form_types=("8-K",),
                since=None, limit=10,
            )
            stored_here = 0
            for m in metas:
                if filings_archive.exists(m.accession):
                    continue
                body = sec_filings.download_filing(m)
                if body is None:
                    continue
                if "<html" in body[:500].lower() or "<!doctype" in body[:200].lower():
                    body = sec_filings.strip_html(body)
                filings_archive.store(
                    symbol=sym, form_type=m.form_type,
                    accession=m.accession, filed_at=m.filed_at,
                    url=m.archive_url, text=body,
                    items=m.items, source="sec_edgar",
                    title=m.primary_doc_description,
                )
                stored_here += 1
            n_new += stored_here
            print(f"  {sym:6s} archived {stored_here} new 8-K(s) "
                  f"({(time.time()-t0)*1000:.0f}ms)")
        print(f"=== done · {n_new} new filings archived ===")
        return 0

    # Full reactor path
    from trader.earnings_reactor import react_for_positions
    results = react_for_positions(
        symbols, since_days=args.since_days,
        only_material=not args.all_8k,
        model=args.model,
        alert=not args.no_alerts,
    )
    n_total = sum(len(rs) for rs in results.values())
    n_material = sum(1 for rs in results.values() for r in rs
                      if r.materiality >= 3)
    print(f"=== done · {n_total} new signals · "
           f"{n_material} material (≥3) ===")
    for sym, rs in results.items():
        if not rs:
            continue
        for r in rs:
            tag = "ERR" if r.error else f"M{r.materiality}"
            cost = f" ${r.cost_usd:.4f}" if r.cost_usd else ""
            print(f"  [{tag}] {sym:6s} {r.filed_at} {r.direction:<10s} "
                  f"items={','.join(r.items) or '-':<10s}{cost}  "
                  f"{r.summary[:80]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
    except Exception as e:
        print(f"reactor failed: {type(e).__name__}: {e}")
        sys.exit(0)  # never block calling cron
