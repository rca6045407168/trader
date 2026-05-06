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
import os
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
from .alerts import alert_halt, alert_kill_switch
from .validation import validate_targets, DataQualityError
from .reconcile import reconcile
from .report import (
    build_daily_report, fetch_alpaca_position_dicts, fetch_spy_today_return,
    fetch_yesterday_equity, fetch_recent_snapshots, fetch_sleeve_pnl,
)
from .anomalies import scan_anomalies
from datetime import date as _date

# Sleeve allocations — v3.0 reverted to fixed 80/20 (v0.5 walk-forward tested config).
# Why: v1.2 risk-parity priors over-allocated to bottom-catch (60%) but bottom-catch
# fires <10% of days, so most of that capital sat as IDLE CASH. 3-month live backfill
# (Jan-Apr 2026) showed cash drag cost ~$10k of unrealized momentum profit.
# The prior monthly vol (4.2%) reflects bottom-catch when ACTIVE, not the blended
# cash-most-of-the-time reality. Risk-parity bug. Until fixed (v3.x), use static 80/20.
# v0.5 walk-forward tested fixed 80/20: CAGR 25.9%, Sharpe 1.41, MaxDD -20.2% OOS.
USE_RISK_PARITY = False
FALLBACK_MOMENTUM_ALLOC = 0.80
FALLBACK_BOTTOM_ALLOC = 0.20
MAX_BOTTOMS_TO_DEBATE = 5


def build_targets(universe: list[str]) -> tuple[dict[str, float], list[dict], dict]:
    """Return (target_weights_for_momentum, list_of_approved_bottoms_with_meta, sleeve_alloc).

    v3.6: momentum targets are produced by the registered LIVE variant function so
    the A/B variant registry IS the source of truth for production. Falls back to
    rank_momentum(top_n=TOP_N) only if no LIVE variant is registered.

    Before v3.6 the variant registry was decorative — main.py used TOP_N from
    config independently, which silently drifted from the registered LIVE
    variant's parameters (caught 2026-04-29: prod was running top-5 while
    LIVE metadata claimed top-3 since v3.1).
    """
    print(f"[{datetime.now():%H:%M:%S}] ranking momentum on {len(universe)} tickers...")
    # Call rank_momentum once to fill candidate metadata for journaling
    momentum_full = rank_momentum(universe, top_n=20)
    momentum = momentum_full[:TOP_N]  # default fallback shape
    print(f"  -> {len(momentum)} momentum picks (default top-{TOP_N}): {[c.ticker for c in momentum]}")

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

    # v3.6: prefer the registered LIVE variant as source of truth.
    try:
        from . import variants  # noqa: F401  (registers variants on import)
        from .ab import get_live
        live = get_live()
    except Exception as e:
        print(f"  variant registry unavailable ({e}); falling back to TOP_N config")
        live = None

    if live is not None:
        try:
            live_targets = live.fn(universe=universe, equity=0.0, account_state={})
            if live_targets:
                # Live variant produces (ticker → weight) directly. Use that as
                # ground truth. Journal each pick with metadata from rank_momentum.
                cand_by_ticker = {c.ticker: c for c in momentum_full}
                momentum_targets = dict(live_targets)
                # Override momentum_alloc to whatever the variant decided —
                # it already encodes its allocation policy.
                momentum_alloc = sum(live_targets.values())
                # Renormalize bottom_alloc to remaining gross capacity
                bottom_alloc = max(0.0, 0.95 - momentum_alloc)
                for ticker, weight in live_targets.items():
                    c = cand_by_ticker.get(ticker)
                    if c is not None:
                        log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None,
                                     final=f"LIVE_VARIANT_BUY @ {weight*100:.1f}% (variant={live.variant_id})")
                    else:
                        # Variant picked a name not in the top-20 — log without metadata
                        log_decision(ticker, "BUY", "live_variant", 0.0,
                                     f"selected by {live.variant_id}", None,
                                     final=f"LIVE_VARIANT_BUY @ {weight*100:.1f}% (variant={live.variant_id})")
                print(f"  -> LIVE variant '{live.variant_id}' chose {len(live_targets)} names: "
                      f"{list(live_targets.keys())} totaling {momentum_alloc*100:.1f}%")
            else:
                print(f"  LIVE variant '{live.variant_id}' returned empty targets — using fallback")
                live = None
        except Exception as e:
            print(f"  LIVE variant '{live.variant_id}' failed ({e}); falling back to TOP_N config")
            live = None

    if live is None or not momentum_targets:
        # Fallback: legacy TOP_N path. v3.73.5 adds STRATEGY_MODE env
        # selection (XS = cross-sectional top-N, the default; or
        # VERTICAL_WINNER = top-1-per-sector with absolute-momentum
        # floor). Vertical-winner is feature-flagged for production
        # A/B per the v3.73.4 DD recommendation.
        import os
        strategy_mode = os.environ.get("STRATEGY_MODE", "XS").upper()
        if strategy_mode == "VERTICAL_WINNER":
            from .strategy import rank_vertical_winner
            vw_picks = rank_vertical_winner(universe)
            print(f"  -> STRATEGY_MODE=VERTICAL_WINNER selected "
                  f"{len(vw_picks)} sector-winners: "
                  f"{[c.ticker for c in vw_picks]}")
            if vw_picks:
                per = momentum_alloc / len(vw_picks)
                for c in vw_picks:
                    momentum_targets[c.ticker] = momentum_targets.get(c.ticker, 0) + per
                    log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None,
                                 final=f"AUTO_BUY @ {per*100:.1f}% (vertical_winner)")
        elif momentum:
            per = momentum_alloc / len(momentum)
            for c in momentum:
                momentum_targets[c.ticker] = momentum_targets.get(c.ticker, 0) + per
                log_decision(c.ticker, c.action, c.style, c.score, c.rationale, None,
                             final=f"AUTO_BUY @ {per*100:.1f}%")

    # v3.73.5: apply portfolio caps (8% single-name, 25% sector). Per
    # the DD analysis, the sector cap is the binding one today (live
    # book is 28.4% Tech). The name cap is defensive for any future
    # move to top-N < 12 or score-weighted sizing. apply_portfolio_caps
    # is a no-op when no cap binds.
    if momentum_targets:
        from .portfolio_caps import apply_portfolio_caps
        from .sectors import get_sector
        cap_result = apply_portfolio_caps(
            momentum_targets, get_sector,
        )
        if cap_result.name_cap_bound or cap_result.sector_cap_bound:
            print(f"  -> portfolio caps: {cap_result.summary()}")
            momentum_targets = cap_result.targets

    # v3.73.17: optional vol-target overlay. Disabled by default
    # (VOL_TARGET_ENABLED unset or "0"). When enabled, computes
    # trailing 60-day portfolio vol from current weights and scales
    # gross down if realized vol > 18%. Never scales up.
    if momentum_targets and os.environ.get("VOL_TARGET_ENABLED", "0") == "1":
        try:
            from .sizing import (
                realized_portfolio_vol_daily, vol_target_scalar, apply_vol_target,
            )
            from .data import fetch_history
            import pandas as pd
            end_d = pd.Timestamp.today()
            start_d = (end_d - pd.DateOffset(months=3)).strftime("%Y-%m-%d")
            syms = list(momentum_targets.keys())
            p = fetch_history(syms, start=start_d).dropna(axis=1, how="any")
            if len(p) >= 30:
                daily_port_rets = []
                for i in range(1, len(p)):
                    r = 0.0
                    for sym, w in momentum_targets.items():
                        if sym not in p.columns:
                            continue
                        p0, p1 = p[sym].iloc[i - 1], p[sym].iloc[i]
                        if p0 > 0:
                            r += w * (p1 / p0 - 1)
                    daily_port_rets.append(r)
                realized = realized_portfolio_vol_daily(daily_port_rets)
                scalar = vol_target_scalar(realized, target_vol=0.18)
                if scalar < 0.99:
                    print(f"  -> vol-target overlay: realized "
                          f"{realized*100:.1f}% > 18% target → "
                          f"scaling gross by {scalar:.3f}")
                    momentum_targets = apply_vol_target(
                        momentum_targets, realized, target_vol=0.18,
                    )
                else:
                    print(f"  -> vol-target overlay: realized "
                          f"{realized*100:.1f}% ≤ 18% target → no scale")
        except Exception as e:
            print(f"  vol-target overlay failed (non-fatal): "
                  f"{type(e).__name__}: {e}")

    # v3.73.17: per-trade max-loss pre-check (warn-only). Refuses to
    # halt — this is a SOFT gate that surfaces when any single
    # position weight × -25% stress > 1.5% of book. With current 8%
    # name cap, max stress loss is 8% × 25% = 2.0%, slightly above
    # the 1.5% threshold; expect this to log a warning on
    # near-cap positions.
    if momentum_targets:
        try:
            from .sizing import max_loss_check
            violations = max_loss_check(
                momentum_targets, max_loss_pct=0.015, stress_pct=0.25,
            )
            if violations:
                print(f"  -> max-loss WARNING: {len(violations)} "
                      f"positions could lose >1.5% on -25% stress:")
                for v in violations[:5]:
                    print(f"     {v.ticker}: w={v.weight*100:.2f}% "
                          f"→ stress_loss={v.stress_loss_pct*100:.2f}%")
        except Exception as e:
            print(f"  max-loss check failed (non-fatal): "
                  f"{type(e).__name__}: {e}")

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

    # v3.46: override-delay pre-flight. If LIVE config changed in last 24h,
    # refuse to rebalance — forces 24h cool-down between "I want to change LIVE"
    # and "the change actually takes effect."
    print(f"\n[{datetime.now():%H:%M:%S}] override-delay check...")
    try:
        from .override_delay import check_override_delay
        allowed, reason = check_override_delay()
        print(f"  {reason}")
        if not allowed:
            return {"halted": True, "kill_switch_reasons": [reason], "halt_type": "override_delay"}
    except Exception as e:
        print(f"  override-delay check failed (non-fatal): {e}")

    # v3.46: peek counter — track manual workflow_dispatch events
    try:
        from .peek_counter import record_event_if_manual, peek_alert_message
        was_manual, peek_count_30d = record_event_if_manual()
        if was_manual:
            print(f"  [PEEK] manual trigger detected. Count in last 30d: {peek_count_30d}")
        alert = peek_alert_message(peek_count_30d)
        if alert:
            print(f"  {alert}")
    except Exception as e:
        print(f"  peek_counter failed (non-fatal): {e}")

    # v0.9: kill-switch pre-flight (manual halt, missing keys, equity drawdown triggers)
    print(f"\n[{datetime.now():%H:%M:%S}] kill-switch pre-flight...")
    try:
        live_equity = float(get_client().get_account().equity) if not DRY_RUN else 100_000.0
    except Exception:
        live_equity = None

    # v3.46: ensure deployment anchor is set on first run (otherwise risk_manager's
    # deployment-DD gates can't fire)
    if live_equity is not None:
        try:
            from .deployment_anchor import get_or_set_anchor
            anchor = get_or_set_anchor(live_equity)
            print(f"  deployment anchor: ${anchor.equity_at_deploy:,.0f} "
                  f"(set {anchor.deploy_timestamp})")
        except Exception as e:
            print(f"  deployment_anchor unavailable (non-fatal): {e}")

    halt, reasons = check_kill_triggers(equity=live_equity)
    if halt:
        for r in reasons:
            print(f"  HALT: {r}")
        # v2.7: structured kill-switch alert (bypasses stub guard via 80+ char body)
        try:
            alert_kill_switch(reasons)
        except Exception as e:
            print(f"  alert_kill_switch failed: {e}")
        return {"halted": True, "kill_switch_reasons": reasons}
    print("  kill switch clear.")

    # v1.9 (B9 partial fix wired in): reconciliation pre-flight
    if not DRY_RUN:
        try:
            client = get_client()
            rep = reconcile(client)
            if rep["halt_recommended"]:
                print(f"  Reconciliation HALT: {rep['summary']}")
                for x in rep["unexpected"][:3]:
                    print(f"    UNEXPECTED: {x['symbol']} qty {x.get('actual_qty')}")
                for x in rep["missing"][:3]:
                    print(f"    MISSING: {x['symbol']} qty {x.get('expected_qty')}")
                # v2.7: rich halt alert with structured detail
                try:
                    alert_halt(
                        reason=f"Reconciliation drift: {rep['summary']}",
                        detail={
                            "unexpected": [x["symbol"] for x in rep["unexpected"]],
                            "missing": [x["symbol"] for x in rep["missing"]],
                            "size_mismatches": len(rep["size_mismatch"]),
                        },
                    )
                except Exception as e:
                    print(f"  alert_halt failed: {e}")
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

    # v3.58.1 — EarningsRule: trim positions whose earnings hit T-N days.
    # Only fires when status() == LIVE; SHADOW logs the would-trim list
    # without actually trimming. Failsafe: any error keeps targets unchanged.
    try:
        from .v358_world_class import EarningsRule
        # v3.63.0: switched from yfinance-only events_calendar to the
        # multi-source earnings_calendar module which falls back through
        # Polygon → Finnhub → AlphaVantage → yfinance. Fixes the v3.58.1
        # INERT bug where yfinance.Ticker.earnings_dates silently returns
        # empty for major tickers (AAPL, NVDA, MSFT, etc).
        from .earnings_calendar import next_earnings_date as _next_earnings, status as _earnings_status
        er = EarningsRule()
        if er.status() in ("LIVE", "SHADOW") and final_targets:
            symbols = list(final_targets.keys())
            today = datetime.utcnow()
            es = _earnings_status()
            if not es.get("any_paid_source_configured"):
                print(f"  EarningsRule warning: no paid earnings source "
                      f"(POLYGON_API_KEY / FINNHUB_API_KEY / ALPHA_VANTAGE_KEY) "
                      f"configured. Falling back to yfinance which silently "
                      f"fails for major tickers.")
            sym_to_earnings: dict[str, datetime] = {}
            for sym in symbols:
                edate = _next_earnings(sym, days_ahead=er.days_before + 1)
                if edate:
                    sym_to_earnings[sym] = datetime.combine(edate, datetime.min.time())
            trimmed = {}
            for sym, weight in list(final_targets.items()):
                edate = sym_to_earnings.get(sym)
                if edate and er.needs_trim(today, edate):
                    new_w = weight * er.trim_to_pct_of_target
                    trimmed[sym] = (weight, new_w, edate.date().isoformat())
                    if er.status() == "LIVE":
                        final_targets[sym] = new_w
            if trimmed:
                action = "TRIMMED" if er.status() == "LIVE" else "would trim (SHADOW)"
                print(f"  EarningsRule {action} {len(trimmed)} positions:")
                for s, (old, new, d) in trimmed.items():
                    print(f"    {s}: {old:.3f} → {new:.3f} (earnings {d})")
            else:
                print(f"  EarningsRule: no positions in trim window "
                      f"(checked {len(symbols)} symbols).")
    except ImportError:
        pass
    except Exception as e:
        print(f"  EarningsRule check failed (non-fatal): {type(e).__name__}: {e}")

    # v3.69.0 — ReactorSignalRule: trim positions when the v3.68.x earnings
    # reactor flagged a high-materiality BEARISH event in the last 14 days.
    # Default status SHADOW (logs would-be trims; caller flips to LIVE via
    # REACTOR_RULE_STATUS=LIVE env when ready). Direction-gated (BULLISH
    # never auto-boosts), materiality-gated (M≥4 default), and bounded
    # (trim to 50% of target — never to 0).
    try:
        from .reactor_rule import ReactorSignalRule
        rsr = ReactorSignalRule()
        if rsr.status() != "INERT" and final_targets:
            new_targets, trims = rsr.apply(final_targets)
            if trims:
                action = ("TRIMMED" if rsr.status() == "LIVE"
                          else "would trim (SHADOW)")
                print(f"  ReactorSignalRule {action} {len(trims)} positions:")
                for sym, d in trims.items():
                    print(f"    {sym}: {d.old_weight:.3f} → {d.new_weight:.3f}  "
                          f"({d.reason})")
                    print(f"      summary: {d.summary[:100]}")
                if rsr.status() == "LIVE":
                    final_targets = new_targets
            else:
                # Quiet log when nothing matches — keeps the run output clean
                pass
    except ImportError:
        pass
    except Exception as e:
        print(f"  ReactorSignalRule check failed (non-fatal): {type(e).__name__}: {e}")

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

        # Re-scan for daily picks/candidates so the report has the structured data.
        # v3.50.2 FIX: was top_n=TOP_N (=3), which made the per-pick rationale
        # table in decision_report show only 3 names instead of all 15 LIVE picks.
        # Pull top-20 so every name in final_targets gets a 'why' row.
        from .strategy import rank_momentum, find_bottoms
        momentum_picks_for_report = rank_momentum(universe, top_n=20)
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
    # v2.9: run shadow strategy variants in parallel with live (logs only, no orders)
    try:
        from . import variants  # registers variants on import
        from .ab import run_shadows
        market_context = {"spy_today_return": spy_today, "vix": vix,
                          "equity": equity_after, "yesterday_equity": yest_eq}
        shadow_results = run_shadows(
            universe=universe, equity=equity_after,
            account_state={"positions": positions_now or {}},
            market_context=market_context,
        )
        if shadow_results:
            print(f"\n[{datetime.now():%H:%M:%S}] shadow variants logged: {list(shadow_results.keys())}")
    except Exception as e:
        print(f"  shadow run failed (non-fatal): {e}")

    # v3.50.1: write per-run permanent Markdown decision report.
    # Independent of email — survives if SMTP isn't configured. Diffable
    # across runs. Rendered in the dashboard's Reports tab.
    try:
        from .decision_report import write_report, RunContext
        # Try to capture overlay signal (re-compute since main.py doesn't
        # currently thread it through; cheap with cached underlying signals).
        overlay_dict = None
        try:
            from .regime_overlay import compute_overlay
            sig = compute_overlay()
            overlay_dict = {
                "enabled": sig.enabled, "final_mult": sig.final_mult,
                "hmm_mult": sig.hmm_mult, "hmm_regime": sig.hmm_regime,
                "hmm_posterior": sig.hmm_posterior,
                "macro_mult": sig.macro_mult,
                "macro_curve_inverted": sig.macro_curve_inverted,
                "macro_credit_widening": sig.macro_credit_widening,
                "garch_mult": sig.garch_mult,
                "garch_vol_forecast_annual": sig.garch_vol_forecast_annual,
            }
        except Exception:
            pass
        rep_ctx = RunContext(
            run_id=run_id,
            started_at=run_id.split("-FORCE")[0].replace(datetime.utcnow().date().isoformat(), datetime.utcnow().isoformat()) or run_id,
            momentum_picks=[{"ticker": c.ticker, "score": c.score, "action": c.action,
                              "style": c.style, "rationale": c.rationale}
                             for c in (momentum_picks_for_report if 'momentum_picks_for_report' in dir() else [])],
            bottom_candidates=[{"ticker": c.ticker, "score": c.score, "rationale": c.rationale}
                                for c in (bottom_candidates_for_report if 'bottom_candidates_for_report' in dir() else [])],
            approved_bottoms=approved_bottoms,
            sleeve_alloc=sleeve_alloc,
            final_targets=final_targets,
            risk_warnings=risk.warnings,
            rebalance_results=rebalance_results,
            bracket_results=bracket_results,
            vix=vix,
            equity_before=equity,
            equity_after=equity_after if 'equity_after' in dir() else None,
            cash_after=cash_after if 'cash_after' in dir() else None,
            positions_now=positions_now if 'positions_now' in dir() else None,
            spy_today_return=spy_today if 'spy_today' in dir() else None,
            yesterday_equity=yest_eq if 'yest_eq' in dir() else None,
            anomalies_today=anomalies if 'anomalies' in dir() else None,
            overlay_signal=overlay_dict,
            shadow_results=shadow_results if 'shadow_results' in dir() else None,
        )
        report_path = write_report(rep_ctx)
        print(f"  decision report written: {report_path}")
    except Exception as e:
        print(f"  decision_report write failed (non-fatal): {type(e).__name__}: {e}")

    # v3.73.7: write a row to strategy_eval for every candidate
    # strategy on every rebalance run. The eval runner is cheap
    # (pure functions on the same price panel; ~1-2s for all 15),
    # and accumulating rows is what turns the leaderboard into a
    # real signal over time. Failures here MUST NOT fail the run.
    #
    # v3.73.14: also fetch ETFs (VTI/VXUS/BND/AGG) alongside SPY so
    # the passive baselines (buy_and_hold_spy, boglehead_three_fund,
    # simple_60_40) can price their picks. The active stock-picking
    # strategies filter to the stock universe via _stock_panel(),
    # so adding ETFs to the panel doesn't contaminate them.
    try:
        from .eval_runner import evaluate_at, settle_returns
        from .data import fetch_history
        import pandas as pd
        end = pd.Timestamp.today()
        start = (end - pd.DateOffset(months=18)).strftime("%Y-%m-%d")
        ETF_TICKERS = ["SPY", "VTI", "VXUS", "BND", "AGG"]
        prices = fetch_history(universe + ETF_TICKERS, start=start)
        prices = prices.dropna(axis=1, how="any")
        if not prices.empty:
            asof = prices.index[-1]
            # Pass full panel (incl. ETFs) so passive baselines work;
            # stock strategies filter to universe internally.
            n = evaluate_at(asof, universe, prices=prices)
            print(f"  -> strategy_eval: recorded {n} new picks for {asof.date()}")
            settled = settle_returns(asof, prices=prices)
            if settled:
                print(f"  -> strategy_eval: settled {settled} prior windows")
    except Exception as e:
        print(f"  strategy_eval hook failed (non-fatal): {type(e).__name__}: {e}")

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
