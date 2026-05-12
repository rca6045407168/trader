#!/usr/bin/env python3
"""Pre-flip rehearsal: what would Day 1 on BROKER=public_live look like?

The go-live gate (scripts/go_live_gate.py) verifies preconditions.
This script answers the complementary question: given those
preconditions are met, what EXACTLY would the orchestrator do on
the first daily-run after BROKER=public_live is flipped?

What this script does:
  1. Forces BROKER=public_live in-process (does NOT touch launchctl env;
     scoped to this Python process only).
  2. Reads account / positions / clock via the PublicAdapter — proving
     end-to-end Public.com connectivity.
  3. Computes what the auto-router would produce as targets, given the
     current universe.
  4. Computes the order PLAN — deltas vs current Public.com positions,
     filtered by the $50 min-order threshold.
  5. Prints a structured rehearsal report.
  6. NEVER submits orders. NEVER mutates the journal. NEVER changes
     the BROKER env outside this process.

What this script does NOT do:
  - Reconcile against journal (the reconcile path is a separate
    concern; use scripts/run_reconcile.py for that).
  - Execute trades (that's the actual flip).
  - Validate go-live gate state (use scripts/go_live_gate.py).
  - Honor TLH_ENABLED, INSIDER_*, PEAD_*, etc. — these are the
    operator's choice for live deployment, separately gated. This
    script previews the BROKER swap only.

Usage:
  python scripts/first_live_dry_run.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Load .env so PUBLIC_API_SECRET + PUBLIC_ACCOUNT_NUMBER are available.
# Same pattern as scripts/test_public_connection.py.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)


def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-order-usd", type=float, default=50.0,
                     help="Skip orders below this notional (matches "
                          "place_target_weights default)")
    args = ap.parse_args(argv)

    print("FIRST-LIVE-RUN DRY REHEARSAL  (BROKER=public_live, NO ORDERS)")
    print()
    print("This script previews Day 1 post-flip. It will:")
    print("  • Read your real Public.com account state")
    print("  • Compute what the daily-run would WANT to do")
    print("  • Show the order plan WITHOUT submitting anything")
    print("  • Leave your launchctl env UNCHANGED (BROKER stays whatever")
    print("    it was; this script's BROKER override is process-local)")
    print()

    # 1. Force BROKER=public_live for this process only
    os.environ["BROKER"] = "public_live"
    from trader.broker import (  # noqa: E402
        get_broker_client, reset_broker_client_for_testing,
    )
    reset_broker_client_for_testing()

    # 2. Read account state via PublicAdapter
    try:
        broker = get_broker_client()
    except Exception as e:
        print(f"❌ FAILED to instantiate PublicAdapter: "
              f"{type(e).__name__}: {e}")
        print()
        print("Run scripts/test_public_connection.py to diagnose creds.")
        return 1

    banner("ACCOUNT STATE (Public.com, live read)")
    try:
        account = broker.get_account()
        print(f"  account_id:      {account.account_id}")
        print(f"  equity:          ${account.equity:>14,.2f}")
        print(f"  cash:            ${account.cash:>14,.2f}")
        print(f"  buying_power:    ${account.buying_power:>14,.2f}")
    except Exception as e:
        print(f"❌ FAILED to fetch account: {type(e).__name__}: {e}")
        return 1

    banner("MARKET CLOCK")
    try:
        clock = broker.get_clock()
        print(f"  is_open:         {clock.is_open}")
        print(f"  next_open:       {clock.next_open}")
        print(f"  next_close:      {clock.next_close}")
        if not clock.is_open:
            print()
            print("  ⚠️  Market is closed. If you flip BROKER=public_live")
            print("     now, the market-open gate will halt the daily-run.")
            print("     Set ALLOW_WEEKEND_ORDERS=1 to override or wait.")
    except Exception as e:
        print(f"❌ FAILED to fetch clock: {type(e).__name__}: {e}")
        return 1

    banner("CURRENT POSITIONS (Public.com)")
    try:
        positions = broker.get_all_positions()
    except Exception as e:
        print(f"❌ FAILED to fetch positions: {type(e).__name__}: {e}")
        return 1

    if not positions:
        print("  (no positions yet — Public.com account is empty)")
    else:
        total_mv = sum(p.market_value for p in positions)
        print(f"  {'Symbol':<8} {'Qty':>10}  {'Avg cost':>10}  "
              f"{'Last':>10}  {'Market value':>14}  {'P&L %':>8}")
        print(f"  {'-'*8} {'-'*10}  {'-'*10}  {'-'*10}  "
              f"{'-'*14}  {'-'*8}")
        for p in sorted(positions, key=lambda x: -x.market_value):
            print(f"  {p.symbol:<8} {p.qty:>10.4f}  "
                  f"${p.avg_entry_price:>9,.2f}  "
                  f"${p.current_price:>9,.2f}  "
                  f"${p.market_value:>13,.2f}  "
                  f"{p.unrealized_plpc * 100:>+7.2f}%")
        print(f"  {'-'*8}")
        print(f"  TOTAL market value: ${total_mv:>14,.2f}")

    # 3. Compute strategy targets — what would build_targets return?
    banner("STRATEGY TARGETS (what auto-router would produce)")
    try:
        from trader.universe import DEFAULT_LIQUID_50, DEFAULT_LIQUID_EXPANDED
        univ_size = os.environ.get("UNIVERSE_SIZE", "").lower()
        if univ_size == "expanded":
            universe = DEFAULT_LIQUID_EXPANDED
        elif univ_size == "sp500_500":
            from trader.universe import sp500_tickers
            universe = sp500_tickers()
        else:
            universe = DEFAULT_LIQUID_50
        print(f"  universe size:   {len(universe)} names "
              f"(UNIVERSE_SIZE={univ_size or '(default)'})")

        from trader.main import build_targets
        momentum_targets, approved_bottoms, sleeve_alloc = build_targets(universe)
        print(f"  momentum targets: {len(momentum_targets)} names, "
              f"gross={sum(momentum_targets.values()) * 100:.1f}%")
        print(f"  bottom-catches:  {len(approved_bottoms)} approved")
        print(f"  sleeve alloc:    {sleeve_alloc}")

        if momentum_targets:
            print()
            print(f"  {'Symbol':<8} {'Target %':>10}")
            for sym, w in sorted(momentum_targets.items(),
                                   key=lambda kv: -kv[1])[:15]:
                print(f"  {sym:<8} {w * 100:>9.2f}%")
            if len(momentum_targets) > 15:
                print(f"  ... and {len(momentum_targets) - 15} more")
    except Exception as e:
        print(f"❌ FAILED to compute targets: {type(e).__name__}: {e}")
        return 1

    # 4. Build the order plan
    banner("ORDER PLAN (notional deltas, $50 min threshold)")
    pos_by_sym = {p.symbol: p.market_value for p in positions}
    equity = account.equity
    if equity <= 0:
        print("  ⚠️  Account equity is $0 — can't compute deltas.")
        print("     Fund the account before flipping BROKER=public_live.")
        return 0

    plan: list[dict] = []
    skipped: list[dict] = []
    closes: list[str] = []

    # Close any position not in targets
    for sym in pos_by_sym:
        if sym not in momentum_targets and pos_by_sym[sym] > 0:
            closes.append(sym)

    # Adjust each target
    for sym, target_pct in momentum_targets.items():
        target_value = equity * target_pct
        current_value = pos_by_sym.get(sym, 0.0)
        delta = target_value - current_value
        if abs(delta) < args.min_order_usd:
            skipped.append({"symbol": sym, "delta": delta,
                              "reason": "below_min"})
            continue
        plan.append({
            "symbol": sym,
            "side": "buy" if delta > 0 else "sell",
            "notional": round(abs(delta), 2),
            "current_value": round(current_value, 2),
            "target_value": round(target_value, 2),
        })

    if closes:
        print(f"  Closes (position not in target set): {len(closes)}")
        for sym in closes:
            print(f"    CLOSE {sym}  current=${pos_by_sym[sym]:,.2f}")
        print()

    if plan:
        plan.sort(key=lambda r: -r["notional"])
        n_buy = sum(1 for r in plan if r["side"] == "buy")
        n_sell = sum(1 for r in plan if r["side"] == "sell")
        total_buy = sum(r["notional"] for r in plan if r["side"] == "buy")
        total_sell = sum(r["notional"] for r in plan if r["side"] == "sell")
        print(f"  {n_buy} BUY ({n_buy and total_buy or 0:,.2f} notional), "
              f"{n_sell} SELL ({n_sell and total_sell or 0:,.2f} notional)")
        print()
        print(f"  {'Symbol':<8} {'Side':<4}  {'Notional':>12}  "
              f"{'Current':>12}  {'Target':>12}")
        for r in plan:
            print(f"  {r['symbol']:<8} {r['side'].upper():<4}  "
                  f"${r['notional']:>11,.2f}  "
                  f"${r['current_value']:>11,.2f}  "
                  f"${r['target_value']:>11,.2f}")
    else:
        print("  No orders would fire — current positions match targets.")

    if skipped:
        print()
        print(f"  Skipped (below ${args.min_order_usd:.0f}): {len(skipped)}")

    # 5. Risk preview
    banner("RISK / SAFETY GATES (preview)")
    gross = sum(momentum_targets.values()) if momentum_targets else 0
    print(f"  Target gross:    {gross * 100:.1f}%")
    print(f"  Cash buffer:     {(1 - gross) * 100:.1f}%")
    if gross > 0.95:
        print(f"  ⚠️  Target gross exceeds 95% — risk_manager would scale "
              f"down.")
    elif gross < 0.50:
        print(f"  ⚠️  Target gross < 50% — system is unusually defensive.")

    # 6. Final summary
    banner("REHEARSAL SUMMARY")
    print(f"  Public.com connectivity:  ✅ verified")
    print(f"  Account funded:           {'✅' if equity > 100 else '⚠️ '} "
          f"${equity:,.2f}")
    print(f"  Market open right now:    {'✅' if clock.is_open else '❌'}")
    print(f"  Order plan size:          {len(plan)} orders, "
          f"{sum(r['notional'] for r in plan):,.2f} notional")
    print(f"  Positions to close:       {len(closes)}")

    # Critical safety check: all targets zero indicates a HALT-class
    # condition (e.g. drawdown protocol enforced, kill-switch tripped).
    all_zero = momentum_targets and all(
        abs(w) < 1e-9 for w in momentum_targets.values()
    )
    if all_zero:
        print()
        print("  ❌ ALL TARGETS ZERO — a HALT-class gate triggered during")
        print("     target construction. Common causes:")
        print()
        print("     1. Drawdown protocol thinks we're in CATASTROPHIC tier.")
        print("        It compares CURRENT equity to ALL-TIME peak in the")
        print("        journal. The journal's peak ($111k from Alpaca paper)")
        print("        is being compared to Public.com's equity ($20) →")
        print("        false -99.98% drawdown → CATASTROPHIC.")
        print("        FIX: reset deployment_anchor for Public.com OR")
        print("        scope journal snapshots by broker before the flip.")
        print()
        print("     2. Kill-switch triggered on some other safety check.")
        print("        Check the lines preceding 'momentum targets:' in")
        print("        the STRATEGY TARGETS section above.")
        print()
        print("     ⚠️  DO NOT FLIP BROKER=public_live until this resolves.")
        print("        The daily-run would HALT immediately or, worse,")
        print("        liquidate whatever positions exist on Public.com.")
    print()
    print("  If you flip BROKER=public_live and kickstart the daemon NOW,")
    print("  the above is approximately what the next daily-run would do.")
    print("  Approximations:")
    print("    • Strategy targets may differ at run-time (intraday data)")
    print("    • Reconcile-drift halt may fire if journal-vs-broker mismatch")
    print("    • Market-open gate may halt (see clock above)")
    print()
    print("  This dry-run did NOT submit any orders. Your broker state and")
    print("  launchctl env are unchanged.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
