"""Run the earnings reactor for current LIVE positions (v3.68.0+).

Usage:
    python scripts/earnings_reactor.py                  # one-shot, all live positions
    python scripts/earnings_reactor.py --symbol NVDA    # one symbol
    python scripts/earnings_reactor.py --since-days 30  # wider lookback
    python scripts/earnings_reactor.py --skip-claude    # archive only, no LLM
    python scripts/earnings_reactor.py --watch          # daemon: poll every 5 min forever

Reads current Alpaca positions to know which symbols matter, fetches
recent SEC 8-Ks for each, archives + analyzes new material filings,
writes structured signals into journal.earnings_signals, and emails
on M≥3 (per v3.68.2 alert layer).

Idempotent at every layer: re-running on the same day is safe — already
archived filings are skipped, already analyzed signals are skipped,
already notified signals are not re-emailed.

In --watch mode (v3.68.3) this becomes a long-running daemon. The
launchd plist installs it that way by default — KeepAlive=true so it
restarts on any crash. This replaces the old StartInterval=4h pattern
with sub-5-min latency between filing and email.
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


_SHUTDOWN = False


def _install_signal_handlers():
    """Catch SIGTERM/SIGINT cleanly so launchd's restart cycle stays
    healthy (no zombie process after crash, exit code reflects intent)."""
    import signal as _signal
    def _handler(signum, frame):
        global _SHUTDOWN
        print(f"  ! received signal {signum}, exiting cleanly after current iter",
              flush=True)
        _SHUTDOWN = True
    _signal.signal(_signal.SIGTERM, _handler)
    _signal.signal(_signal.SIGINT, _handler)


def _watch_loop(symbols: list[str], args) -> int:
    """Long-running daemon. Polls every args.watch_interval seconds.
    Same per-iteration logic as one-shot mode + a sleep between iters.

    Emits a per-iteration line to stdout (line-buffered so launchd /
    `tail -f` show progress in real time). On SIGTERM, exits with 0
    after the current iteration completes — never aborts mid-Claude-call."""
    import sys as _sys
    # Force line buffering so logs are usable in real time
    _sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    _install_signal_handlers()

    from trader.earnings_reactor import react_for_positions
    interval = max(60, int(args.watch_interval))  # floor at 1 min
    print(f"=== earnings reactor WATCH mode "
          f"(interval={interval}s, {len(symbols)} symbols, "
          f"model={args.model}) ===")
    print(f"=== ctrl-c or SIGTERM exits cleanly; launchd KeepAlive will "
          f"restart on crash ===")

    iter_n = 0
    while not _SHUTDOWN:
        iter_n += 1
        t0 = time.time()
        try:
            results = react_for_positions(
                symbols, since_days=args.since_days,
                only_material=not args.all_8k,
                model=args.model,
                alert=not args.no_alerts,
            )
            n_new = sum(len(rs) for rs in results.values())
            n_material = sum(1 for rs in results.values() for r in rs
                              if r.materiality >= 3)
            elapsed = time.time() - t0
            ts = datetime.utcnow().isoformat(timespec="seconds")
            print(f"[iter {iter_n:>5d} {ts}Z] {n_new} new signals, "
                  f"{n_material} material · {elapsed:.1f}s", flush=True)
            # Detail lines only when something happened
            for sym, rs in results.items():
                for r in rs:
                    tag = "ERR" if r.error else f"M{r.materiality}"
                    cost = f" ${r.cost_usd:.4f}" if r.cost_usd else ""
                    print(f"  [{tag}] {sym:6s} {r.filed_at} "
                          f"{r.direction:<10s} items={','.join(r.items) or '-':<10s}"
                          f"{cost}  {r.summary[:80]}", flush=True)
        except Exception as e:
            print(f"[iter {iter_n} ERROR] {type(e).__name__}: {e}",
                   flush=True)
            # Don't break on transient errors — launchd KeepAlive can
            # respawn us if things get really bad, but a single SEC
            # 503 shouldn't tear down the daemon.

        # Sleep in 5-sec chunks so SIGTERM gets noticed quickly
        slept = 0
        while slept < interval and not _SHUTDOWN:
            time.sleep(min(5, interval - slept))
            slept += 5
    print("=== watch loop exited cleanly ===")
    return 0


from datetime import datetime
import time


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
    parser.add_argument("--watch", action="store_true",
                         help="Daemon mode: poll every --watch-interval seconds "
                              "forever. Pairs with launchd KeepAlive=true.")
    parser.add_argument("--watch-interval", type=int,
                         default=int(os.getenv("REACTOR_WATCH_INTERVAL", "300")),
                         help="Watch loop poll interval in seconds (default 300 = 5 min). "
                              "Override via REACTOR_WATCH_INTERVAL env.")
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

    if args.watch:
        return _watch_loop(symbols, args)

    # Full reactor path (one-shot)
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
