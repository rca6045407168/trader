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
from .journal import init_db, log_decision, log_order, log_daily_snapshot
from .notify import notify
from .kill_switch import check_kill_triggers
from .validation import validate_targets, DataQualityError

# Sleeve allocations — v0.5 walk-forward result.
# Fixed 60/40 chosen as deployable proxy for risk-parity 2-sleeve (Sharpe 1.38 OOS).
# Risk-parity itself needs 12 months of live monthly returns to bootstrap — v0.6 work.
MOMENTUM_ALLOC = 0.60
BOTTOM_ALLOC = 0.40
MAX_BOTTOMS_TO_DEBATE = 5


def build_targets(universe: list[str]) -> tuple[dict[str, float], list[dict]]:
    """Return (target_weights_for_momentum, list_of_approved_bottoms_with_meta)."""
    print(f"[{datetime.now():%H:%M:%S}] ranking momentum on {len(universe)} tickers...")
    momentum = rank_momentum(universe, top_n=TOP_N)
    print(f"  -> {len(momentum)} momentum picks: {[c.ticker for c in momentum]}")

    print(f"[{datetime.now():%H:%M:%S}] scanning for oversold bounces...")
    bottoms = find_bottoms(universe)
    print(f"  -> {len(bottoms)} bottom candidates: {[(c.ticker, round(c.score, 2)) for c in bottoms]}")

    momentum_targets: dict[str, float] = {}
    if momentum:
        per = MOMENTUM_ALLOC / len(momentum)
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

    return momentum_targets, approved_bottoms


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

    # v0.9: idempotency check — don't re-place orders if we already ran today
    if not force and not DRY_RUN:
        from .journal import recent_snapshots
        today_snaps = recent_snapshots(days=1)
        today_iso = datetime.utcnow().date().isoformat()
        if any(s["date"] == today_iso for s in today_snaps):
            print("  IDEMPOTENT: today's snapshot already exists. Use force=True to re-run.")
            return {"skipped": True, "reason": "already_ran_today"}

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
    momentum_targets, approved_bottoms = build_targets(universe)

    # Combine all targets for portfolio-level risk check
    combined_targets = dict(momentum_targets)
    if approved_bottoms:
        total_bottom = sum(b["position_pct"] for b in approved_bottoms)
        scale = min(1.0, BOTTOM_ALLOC / total_bottom) if total_bottom > 0 else 0
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

    notify(
        f"Run complete. {len(final_targets)} targets, "
        f"{len(rebalance_results)} momentum orders, {len(bracket_results)} bottom-catch brackets."
    )
    return {
        "targets": final_targets,
        "momentum_orders": rebalance_results,
        "bracket_orders": bracket_results,
        "vix": vix,
        "equity": equity,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="override idempotency guard")
    args = parser.parse_args()
    main(force=args.force)
