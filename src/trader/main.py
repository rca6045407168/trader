"""Daily orchestrator. Run after market close.

Pipeline:
  1. Rank universe by momentum -> top N (rule-based, no LLM).
  2. Scan universe for oversold bounces -> candidates.
  3. For each bottom candidate: run Bull/Bear/Risk debate. Risk Manager decides.
  4. Combine: 80% to momentum picks (equal weight), up to 20% to approved bottoms.
  5. Run portfolio risk_manager checks (daily loss, drawdown, vol scaling, position caps).
  6. Build OrderPlans (limit entries, brackets for bottom-catches).
  7. Submit to Alpaca; log everything.

All output is logged to SQLite. The post-mortem agent reads it the next night.
"""
from datetime import datetime

from .config import TOP_N, USE_DEBATE, DRY_RUN
from .universe import DEFAULT_LIQUID_50
from .strategy import rank_momentum, find_bottoms
from .critic import debate
from .execute import place_target_weights, place_bracket_order, get_client, get_last_price, close_aged_bottom_catches
from .order_planner import plan_momentum_entry, plan_bottom_entry
from .risk_manager import check_account_risk
from .journal import init_db, log_decision, log_order, log_daily_snapshot, start_run, finish_run, open_lot
from .notify import notify
from .kill_switch import check_kill_triggers
from .validation import validate_targets, DataQualityError
from .reconcile import reconcile
from .report import (
    build_daily_report, fetch_alpaca_position_dicts, fetch_spy_today_return,
    fetch_yesterday_equity, fetch_recent_snapshots, fetch_sleeve_pnl,
)
from .anomalies import scan_anomalies
from datetime import date as _date

# Sleeve allocations — v1.2 risk-parity with backtest priors.
# OOS results (v1.1 walk-forward 2021-2025):
#   - momentum-only:           CAGR 16.0%  Sharpe 0.74  MaxDD -32.8%
#   - fixed 60/40 (was):       CAGR 25.9%  Sharpe 1.41  MaxDD -20.2%
#   - risk-parity w/ priors:   CAGR 30.6%  Sharpe 1.76  MaxDD -14.6%
# Now deployed via risk_parity.compute_weights()
USE_RISK_PARITY = True
FALLBACK_MOMENTUM_ALLOC = 0.60
FALLBACK_BOTTOM_ALLOC = 0.40
MAX_BOTTOMS_TO_DEBATE = 5


def build_targets(universe: list[str]) -> tuple[dict[str, float], list[dict], dict]:
    """Return (target_weights_for_momentum, list_of_approved_bottoms_with_meta, sleeve_alloc)."""
    print(f"[{datetime.now():%H:%M:%S}] ranking momentum on {len(universe)} tickers...")
    momentum = rank_momentum(universe, top_n=TOP_N)
    print(f"  -> {len(momentum)} momentum picks: {[c.ticker for c in momentum]}")

    print(f"[{datetime.now():%H:%M:%S}] scanning for oversold bounces...")
    bottoms = find_bottoms(universe)
    print(f"  -> {len(bottoms)} bottom candidates: {[(c.ticker, round(c.score, 2)) for c in bottoms]}")

    # v1.2: compute sleeve weights via risk-parity (or fall back to fixed)
    if USE_RISK_PARITY:
        from .risk_parity import compute_weights, compute_sleeve_returns_from_journal
        try:
            mom_hist, bot_hist = compute_sleeve_returns_from_journal()
            sw = compute_weights(mom_hist, bot_hist)
            momentum_alloc, bottom_alloc = sw.momentum, sw.bottom
            print(f"  risk-parity sleeves — momentum: {momentum_alloc:.0%}  bottom: {bottom_alloc:.0%}  ({sw.method})")
        except Exception as e:
            print(f"  risk-parity failed ({e}); falling back to fixed 60/40")
            momentum_alloc, bottom_alloc = FALLBACK_MOMENTUM_ALLOC, FALLBACK_BOTTOM_ALLOC
    else:
        momentum_alloc, bottom_alloc = FALLBACK_MOMENTUM_ALLOC, FALLBACK_BOTTOM_ALLOC

    momentum_targets: dict[str, float] = {}
    if momentum:
        per = momentum_alloc / len(momentum)
        for c in momentum:
            momentum_targets[c.ticker] = momentum_targets.get(c.ticker, 0) + per
            log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None,
                         final=f"AUTO_BUY @ {per*100:.1f}%")

    approved_bottoms: list[dict] = []
    for c in bottoms[:MAX_BOTTOMS_TO_DEBATE]:
        if not USE_DEBATE:
            approved_bottoms.append({"candidate": c, "position_pct": 0.04})
            log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None, "AUTO_BUY (debate off)")
            continue
        try:
            d = debate(c)
            log_decision(c.ticker, c.action, c.style, c.score, c.rationale, d,
                         f"DEBATE -> {d['action']} @ {d['position_pct']*100:.1f}%")
            if d["action"] == "BUY" and d["position_pct"] > 0:
                approved_bottoms.append({"candidate": c, "position_pct": d["position_pct"]})
        except Exception as e:
            print(f"  debate error for {c.ticker}: {e}")
            log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None, f"DEBATE_ERROR: {e}")

    return momentum_targets, approved_bottoms, {"momentum": momentum_alloc, "bottom": bottom_alloc}


def get_vix() -> float | None:
    """Fetch ^VIX last close from yfinance. Returns None on failure."""
    try:
        from .data import fetch_history
        from datetime import timedelta
        end = datetime.now()
        start = (end - timedelta(days=10)).strftime("%Y-%m-%d")
        df = fetch_history(["^VIX"], start=start)
        return float(df.iloc[-1, 0])
    except Exception:
        return None


def main(force: bool = False) -> dict:
    init_db()
    print(f"\n=== trader daily run @ {datetime.now().isoformat()} ===")
    print(f"  TOP_N={TOP_N}  USE_DEBATE={USE_DEBATE}  DRY_RUN={DRY_RUN}")

    # v1.3 (B5 FIX): durable run sentinel BEFORE any orders. Idempotent against
    # crashes between order placement and snapshot.
    run_id = f"{datetime.utcnow().date().isoformat()}-{datetime.utcnow().strftime('%H%M%S')}"
    if not DRY_RUN:
        if not start_run(run_id, notes=f"main() entry; force={force}"):
            if not force:
                print("  IDEMPOTENT: today's run already started/completed. Use --force to re-run.")
                return {"skipped": True, "reason": "already_ran_today"}
            else:
                # force=True: create a new run id so we can still track this attempt
                run_id = f"{run_id}-FORCE"
                start_run(run_id, notes="forced re-run")

    # v0.9: kill-switch pre-flight (manual halt, missing keys, equity drawdown triggers)
    print(f"\n[{datetime.now():%H:%M:%S}] kill-switch pre-flight...")
    try:
        live_equity = float(get_client().get_account().equity) if not DRY_RUN else 100_000.0
    except Exception:
        live_equity = None
    halt, reasons = check_kill_triggers(equity=live_equity)
    if halt:
        for r in reasons:
            print(f"  HALT: {r}")
        notify(f"Kill switch tripped: {'; '.join(reasons)}", level="warn")
        return {"halted": True, "kill_switch_reasons": reasons}
    print("  kill switch clear.")

    # v1.9 (B9 partial fix wired in): reconciliation pre-flight
    if not DRY_RUN:
        try:
            client = get_client()
            rep = reconcile(client)
            if rep["halt_recommended"]:
                msg = f"Reconciliation HALT: {rep['summary']}"
                print(f"  {msg}")
                for x in rep["unexpected"][:3]:
                    print(f"    UNEXPECTED: {x['symbol']} ${x['actual_value']:,.2f}")
                for x in rep["missing"][:3]:
                    print(f"    MISSING: {x['symbol']} ${x['expected_value']:,.2f}")
                notify(msg, level="warn")
                return {"halted": True, "reason": "reconciliation_drift", "detail": rep}
            print(f"  reconcile: {rep['summary']}")
        except Exception as e:
            print(f"  reconcile failed (non-fatal): {e}")

    # v0.7: time-exit aged bottom-catch positions (20 trading days)
    print(f"\n[{datetime.now():%H:%M:%S}] checking for aged bottom-catch positions to close...")
    if not DRY_RUN:
        try:
            aged_closes = close_aged_bottom_catches(max_age_days=20)
            for r in aged_closes:
                print(f"  CLOSING (aged): {r['symbol']} (opened {r.get('opened', 'unknown')[:10]})")
                log_order(r["symbol"], "sell", 0, None, r.get("status", ""), r.get("error"))
        except Exception as e:
            print(f"  aged-close failed: {e}")

    universe = DEFAULT_LIQUID_50
    momentum_targets, approved_bottoms, sleeve_alloc = build_targets(universe)

    # Combine all targets for portfolio-level risk check
    combined_targets = dict(momentum_targets)
    if approved_bottoms:
        total_bottom = sum(b["position_pct"] for b in approved_bottoms)
        scale = min(1.0, sleeve_alloc["bottom"] / total_bottom) if total_bottom > 0 else 0
        for b in approved_bottoms:
            t = b["candidate"].ticker
            combined_targets[t] = combined_targets.get(t, 0) + b["position_pct"] * scale

    # Risk gate — may halt entire run, or scale targets down
    print(f"\n[{datetime.now():%H:%M:%S}] risk check...")
    vix = get_vix()
    if vix is not None:
        print(f"  VIX: {vix:.1f}")

    if DRY_RUN:
        equity = 100_000.0
    else:
        try:
            client = get_client()
            equity = float(client.get_account().equity)
        except Exception as e:
            print(f"  account fetch failed ({e}); using 100k for risk check")
            equity = 100_000.0

    risk = check_account_risk(equity, combined_targets, vix=vix)
    for w in risk.warnings:
        print(f"  WARN: {w}")
    print(f"  decision: proceed={risk.proceed}  {risk.reason}")
    if not risk.proceed:
        notify(f"HALT: {risk.reason}", level="warn")
        return {"halted": True, "reason": risk.reason}

    final_targets = risk.adjusted_targets

    # v0.9: validate targets before any order leaves the system
    try:
        target_check = validate_targets(final_targets)
        for w in target_check["warnings"]:
            print(f"  validation warn: {w}")
    except DataQualityError as e:
        print(f"  HALT: target validation failed — {e}")
        notify(f"Target validation HALT: {e}", level="warn")
        return {"halted": True, "reason": str(e)}

    print("\nFinal target allocation (post-risk):")
    for t, w in sorted(final_targets.items(), key=lambda x: -x[1]):
        print(f"  {t:6s}  {w*100:5.2f}%")
    print(f"  ----  total {sum(final_targets.values())*100:.1f}%")

    # Execute momentum sleeve as target weights (rebalance)
    momentum_only = {t: w for t, w in final_targets.items() if t in momentum_targets}
    print(f"\n[{datetime.now():%H:%M:%S}] placing momentum rebalance (dry_run={DRY_RUN})...")
    try:
        rebalance_results = place_target_weights(momentum_only, dry_run=DRY_RUN)
        for r in rebalance_results:
            log_order(r.get("symbol", ""), r.get("side", ""),
                      r.get("notional", 0), r.get("order_id"),
                      r.get("status", ""), r.get("error"))
        print(f"  -> {len(rebalance_results)} momentum-leg results")
    except Exception as e:
        print(f"  momentum execute failed: {e}")
        rebalance_results = []

    # Execute bottom sleeve as bracket-limit orders (one per approved candidate)
    bracket_results: list[dict] = []
    print(f"\n[{datetime.now():%H:%M:%S}] placing bottom-catch bracket orders...")
    for b in approved_bottoms:
        c = b["candidate"]
        sized_pct = final_targets.get(c.ticker, 0)
        if sized_pct <= 0:
            continue
        notional = round(equity * sized_pct, 2)
        try:
            last_price = get_last_price(c.ticker) if not DRY_RUN else 100.0
            atr_dollar = c.atr_pct * last_price
            plan = plan_bottom_entry(c.ticker, notional, last_price, atr_dollar)
            print(f"  {c.ticker}: {plan.rationale}")
            res = place_bracket_order(plan, dry_run=DRY_RUN)
            bracket_results.append(res)
            log_order(c.ticker, plan.side, plan.notional or 0,
                      res.get("order_id"), res.get("status", ""), res.get("error"))
        except Exception as e:
            print(f"    bracket failed for {c.ticker}: {e}")
            bracket_results.append({"symbol": c.ticker, "status": "error", "error": str(e)})
            log_order(c.ticker, "BUY", 0, None, "error", str(e))

    # Snapshot account
    if not DRY_RUN:
        try:
            client = get_client()
            acct = client.get_account()
            positions = {p.symbol: float(p.market_value) for p in client.get_all_positions()}
            log_daily_snapshot(float(acct.equity), float(acct.cash), positions)
            print(f"\nSnapshot: equity=${float(acct.equity):.2f}  cash=${float(acct.cash):.2f}")
        except Exception as e:
            print(f"  snapshot failed: {e}")

    # v2.3: build the rich email instead of one-liner
    try:
        if not DRY_RUN:
            client = get_client()
            acct = client.get_account()
            equity_after = float(acct.equity)
            cash_after = float(acct.cash)
            positions_now = fetch_alpaca_position_dicts(client)
        else:
            equity_after = equity
            cash_after = equity
            positions_now = {}

        # Re-scan for daily picks/candidates so the report has the structured data
        from .strategy import rank_momentum, find_bottoms
        momentum_picks_for_report = rank_momentum(universe, top_n=TOP_N)
        bottom_candidates_for_report = find_bottoms(universe)

        spy_today = fetch_spy_today_return()
        yest_eq = fetch_yesterday_equity()
        anomalies = scan_anomalies(_date.today())
        recent_snaps = fetch_recent_snapshots(days=30)
        sleeve_pnl = fetch_sleeve_pnl(positions_now or {})

        subject, body = build_daily_report(
            run_id=run_id,
            momentum_picks=momentum_picks_for_report,
            bottom_candidates=bottom_candidates_for_report,
            approved_bottoms=approved_bottoms,
            sleeve_alloc=sleeve_alloc,
            sleeve_method="prior_only" if not yest_eq else "sample",
            final_targets=final_targets,
            risk_warnings=risk.warnings,
            rebalance_results=rebalance_results,
            bracket_results=bracket_results,
            vix=vix,
            equity_before=equity,
            equity_after=equity_after,
            cash_after=cash_after,
            positions_now=positions_now,
            spy_today_return=spy_today,
            yesterday_equity=yest_eq,
            anomalies_today=anomalies,
            sleeve_pnl=sleeve_pnl,
            recent_snapshots=recent_snaps,
            is_first_trading_day=(yest_eq is None),
        )
        notify(body, subject=subject)
    except Exception as e:
        # Fallback to terse notification if the rich report fails
        print(f"  rich-report failed ({e}), falling back to terse notify")
        notify(
            f"Run complete. {len(final_targets)} targets, "
            f"{len(rebalance_results)} momentum orders, {len(bracket_results)} bottom-catch brackets.",
            subject="trader run complete (fallback)"
        )
    if not DRY_RUN:
        finish_run(run_id, status="completed",
                   notes=f"{len(final_targets)} targets, {len(rebalance_results)} mom, {len(bracket_results)} bot")
    return {
        "targets": final_targets,
        "momentum_orders": rebalance_results,
        "bracket_orders": bracket_results,
        "vix": vix,
        "equity": equity,
        "run_id": run_id,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="override idempotency guard")
    args = parser.parse_args()
    main(force=args.force)
