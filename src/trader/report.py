"""Detailed daily report assembly (v2.6).

Designed to answer three questions in one scroll:
  1. Did anything notable happen today?
  2. What's coming up that affects the portfolio?
  3. Should I do anything as the operator?

Followed by detailed data sections for verification.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any
from zoneinfo import ZoneInfo
import calendar
import math
import statistics
import pandas as pd

PT = ZoneInfo("America/Los_Angeles")
STARTING_CAPITAL = 100_000.0
STRATEGY_VERSION = "v2.6"


def _section(title: str, body: str) -> str:
    return f"=== {title} ===\n{body}\n"


def _fmt_pct(x: float | None, plus: bool = True) -> str:
    if x is None:
        return "n/a"
    sign = "+" if plus and x >= 0 else ""
    return f"{sign}{x * 100:.2f}%"


def _fmt_money(x: float, sign: bool = False) -> str:
    if sign:
        return f"${x:+,.2f}"
    return f"${x:,.2f}"


def _now_pt() -> str:
    return datetime.now(PT).strftime("%Y-%m-%d %H:%M %Z")


def _next_monthly_rebalance(today: date) -> date:
    """Last business day of the current month, or next month if past it."""
    last_day = calendar.monthrange(today.year, today.month)[1]
    candidate = date(today.year, today.month, last_day)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    if candidate <= today:
        if today.month == 12:
            ny, nm = today.year + 1, 1
        else:
            ny, nm = today.year, today.month + 1
        last_day = calendar.monthrange(ny, nm)[1]
        candidate = date(ny, nm, last_day)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
    return candidate


def _explain_order(r: dict) -> str:
    """Generate a one-line rationale for an order line."""
    sym = r.get("symbol", "?")
    status = r.get("status", "")
    side = r.get("side", "")
    notional = r.get("notional", 0)
    if status == "below_min":
        return f"  {sym:6s}  HOLD     —  current allocation already within $50 of target (no trade needed)"
    if status == "submitted":
        action = "trim" if side == "sell" else "top-up"
        return f"  {sym:6s}  {side.upper():4s}     {_fmt_money(notional)}  rebalance {action} to bring weight back to target"
    if status == "error":
        return f"  {sym:6s}  ERROR    {_fmt_money(notional)}  {r.get('error', '')}"
    if status == "closed":
        return f"  {sym:6s}  CLOSE    —  not in target portfolio anymore"
    return f"  {sym:6s}  {status:8s}  {_fmt_money(notional)}"


def _scan_upcoming(today: date, days_ahead: int = 7) -> list[Any]:
    """Scan anomalies for today + next N days. Returns list of (date, anomaly)."""
    from .anomalies import scan_anomalies
    out = []
    for offset in range(days_ahead + 1):
        d = today + timedelta(days=offset)
        for a in scan_anomalies(d):
            out.append((d, a))
    return out


def _daily_vol_band(snapshots: list[dict]) -> float | None:
    """Return rolling realized 1-σ daily return from recent snapshots, or None if insufficient."""
    if not snapshots or len(snapshots) < 5:
        return None
    eqs = [s["equity"] for s in snapshots if s.get("equity")]
    if len(eqs) < 5:
        return None
    rets = [eqs[i] / eqs[i + 1] - 1 for i in range(len(eqs) - 1)]
    if len(rets) < 4:
        return None
    return statistics.stdev(rets)


def build_daily_report(
    *,
    run_id: str,
    momentum_picks: list[Any],
    bottom_candidates: list[Any],
    approved_bottoms: list[dict],
    sleeve_alloc: dict[str, float],
    sleeve_method: str,
    final_targets: dict[str, float],
    risk_warnings: list[str],
    rebalance_results: list[dict],
    bracket_results: list[dict],
    vix: float | None,
    equity_before: float,
    equity_after: float,
    cash_after: float,
    positions_now: dict[str, dict] | None,
    spy_today_return: float | None,
    yesterday_equity: float | None,
    anomalies_today: list[Any] | None = None,
    sleeve_pnl: dict[str, dict] | None = None,
    recent_snapshots: list[dict] | None = None,
    is_first_trading_day: bool = False,
    market_open_today: bool = True,
    last_trading_day: date | None = None,
) -> tuple[str, str]:
    """Returns (subject, body)."""
    sections = []

    # ---- COMPUTE ALL THE NUMBERS UP FRONT ----
    deployed = equity_after - cash_after
    deployed_pct = deployed / equity_after if equity_after else 0
    cash_pct = cash_after / equity_after if equity_after else 0
    today = date.today()

    # Day P&L: prefer yesterday's snapshot; fall back to starting capital with disclaimer
    if yesterday_equity:
        day_pnl = equity_after - yesterday_equity
        day_pct = day_pnl / yesterday_equity
        day_basis_note = ""
    else:
        day_pnl = equity_after - STARTING_CAPITAL
        day_pct = day_pnl / STARTING_CAPITAL
        day_basis_note = " (vs $100k start; no prior snapshot yet)"

    # v6.0.x: on a non-trading day (weekend / holiday), the broker's
    # "day P&L" is stale (it's the LAST trading day's P&L). The report
    # used to label it as today's intraday performance, which was
    # misleading. Now we relabel + caveat.
    if not market_open_today:
        last_str = last_trading_day.isoformat() if last_trading_day else "last close"
        day_pnl_label = f"Last-close P&L ({last_str}):"
        day_basis_note = (day_basis_note or "") + " — market closed today; figures are from the last trading session"
    else:
        day_pnl_label = "Day P&L:  "

    cum_pnl = equity_after - STARTING_CAPITAL
    cum_pct = cum_pnl / STARTING_CAPITAL
    excess_return = (day_pct - spy_today_return) if spy_today_return is not None else None

    # v2.7: true alpha (Jensen's) requires beta. We can only compute it once
    # we have ~10+ days of history, so until then we report excess return.
    beta_alpha = None
    try:
        from .perf_metrics import fetch_portfolio_and_spy_returns, compute_beta_alpha
        p_rets, s_rets = fetch_portfolio_and_spy_returns(days=30)
        if len(p_rets) >= 5 and len(s_rets) >= 5:
            beta_alpha = compute_beta_alpha(p_rets, s_rets)
    except Exception:
        beta_alpha = None

    # Drawdown from peak in our snapshot history
    max_eq = STARTING_CAPITAL
    if recent_snapshots:
        all_eqs = [s["equity"] for s in recent_snapshots if s.get("equity")] + [equity_after]
        if all_eqs:
            max_eq = max(max_eq, max(all_eqs))
    dd_from_peak = (equity_after / max_eq) - 1

    # Daily realized vol (rough proxy for "what's normal")
    daily_vol = _daily_vol_band(recent_snapshots or [])
    sigma_today = day_pnl / (yesterday_equity or STARTING_CAPITAL) / daily_vol if daily_vol else None

    # Upcoming events (today + next 7 calendar days)
    upcoming = _scan_upcoming(today, days_ahead=7)
    today_anomalies = anomalies_today or [a for d, a in upcoming if d == today]
    future_anomalies = [(d, a) for d, a in upcoming if d > today]

    # Next monthly rebalance
    next_rebal = _next_monthly_rebalance(today)
    days_to_rebal = (next_rebal - today).days

    # ---- SUBJECT (lead with biggest signal) ----
    subject_parts = []
    if future_anomalies:
        # FOMC tomorrow > today's micro-P&L
        d, a = future_anomalies[0]
        days_until = (d - today).days
        prefix = "TOMORROW" if days_until == 1 else f"{days_until}d"
        subject_parts.append(f"{prefix}: {a.name}")
    subject_parts.append(f"day {_fmt_pct(day_pct)}")
    # v3.50.2 FIX: was 'alpha' (undefined NameError that crashed 3 of 4 recent
    # daily-runs). True Jensen alpha needs 5+ days history; until then we ship
    # excess_return (day P&L vs SPY) which IS defined above.
    if excess_return is not None:
        subject_parts.append(f"excess {_fmt_pct(excess_return)}")
    subject_parts.append(f"equity {_fmt_money(equity_after)}")
    subject = " | ".join(subject_parts)

    # ---- (1) AT-A-GLANCE — answers all 3 questions in 6 lines ----
    notable = []
    if abs(day_pct) > (daily_vol or 0.005) * 2 and daily_vol:
        notable.append(f"day move > 2σ ({_fmt_pct(day_pct)} vs ±{daily_vol*100*2:.2f}% band)")
    if dd_from_peak < -0.05:
        notable.append(f"drawdown {_fmt_pct(dd_from_peak)} from peak")
    submitted = [r for r in rebalance_results if r.get("status") == "submitted"]
    if submitted:
        notable.append(f"{len(submitted)} order(s) executed (rest skipped within rebalance band)")
    if approved_bottoms:
        notable.append(f"{len(approved_bottoms)} bottom-catch trade(s) opened")
    if not notable:
        notable.append("no rotation; micro-rebalance only" if submitted else "no trading activity (allocations already on target)")

    next_event = ""
    if future_anomalies:
        d, a = future_anomalies[0]
        days_until = (d - today).days
        when = "tomorrow" if days_until == 1 else f"in {days_until} days ({d})"
        next_event = f"  Next event: {a.name} {when} ({a.confidence} confidence, +{a.expected_alpha_bps}bps expected)"
    else:
        next_event = f"  Next event: monthly rebalance in {days_to_rebal} days ({next_rebal})"

    action = "No human action recommended — system healthy."
    if dd_from_peak < -0.08:
        action = "REVIEW — drawdown approaches kill-switch threshold (-8%)."
    elif risk_warnings:
        action = "Note vol scaling active; no manual action needed."

    sections.append(_section(
        "AT-A-GLANCE",
        f"Today:    " + "; ".join(notable) + "\n"
        + next_event + "\n"
        f"  Action:  {action}"
    ))

    # ---- (2) ACCOUNT ----
    sigma_str = ""
    if sigma_today is not None and abs(sigma_today) > 0.01:
        sigma_str = f"  ({sigma_today:+.2f}σ vs realized vol band)"
    spy_str = f"SPY {_fmt_pct(spy_today_return)}" if spy_today_return is not None else "SPY n/a"
    excess_str = f", excess {_fmt_pct(excess_return)}" if excess_return is not None else ""
    alpha_lines = []
    if beta_alpha and not math.isnan(beta_alpha.get("beta", float("nan"))):
        alpha_lines.append(
            f"True alpha (Jensen, n={beta_alpha['n_obs']} obs): \u03b2={beta_alpha['beta']:.2f}, "
            f"\u03b1={beta_alpha['alpha_annualized']*100:+.2f}%/yr, "
            f"R\u00b2={beta_alpha['r_squared']:.2f}, TE={beta_alpha['tracking_error']*100:.2f}%/period"
        )
    elif yesterday_equity is None:
        alpha_lines.append("True alpha (Jensen): need >=5 days of history; reporting excess return only")
    sections.append(_section(
        "ACCOUNT",
        f"Equity:    {_fmt_money(equity_after)}\n"
        f"Cash:      {_fmt_money(cash_after)}  ({cash_pct*100:.0f}%)\n"
        f"Deployed:  {_fmt_money(deployed)}  ({deployed_pct*100:.0f}%)\n"
        f"{day_pnl_label} {_fmt_money(day_pnl, sign=True)}  ({_fmt_pct(day_pct)}){day_basis_note}{sigma_str}\n"
        f"           vs {spy_str}{excess_str}\n"
        f"Total:     {_fmt_money(cum_pnl, sign=True)}  ({_fmt_pct(cum_pct)}) since $100k start\n"
        f"Drawdown:  {_fmt_pct(dd_from_peak)} from rolling peak ({_fmt_money(max_eq)})"
        + ("\n" + "\n".join(alpha_lines) if alpha_lines else "")
    ))

    # ---- (3) DECISIONS ----
    decisions_lines = []
    if momentum_picks:
        decisions_lines.append(f"Momentum: top {len(momentum_picks)} by 12-month trailing return (rebalance monthly)")
        for c in momentum_picks:
            ret = c.rationale.get("trailing_return", 0)
            decisions_lines.append(
                f"  {c.ticker:6s}  12m return {_fmt_pct(ret):>8s}  ATR {c.atr_pct*100:.1f}%"
            )
    if bottom_candidates:
        decisions_lines.append(f"\nBottom-catch: {len(bottom_candidates)} candidates passed threshold (score >= 0.65)")
        for c in bottom_candidates[:5]:
            comp = c.rationale
            decisions_lines.append(
                f"  {c.ticker:6s}  score {c.score:.2f}  RSI {comp.get('rsi', 0):.0f}  "
                f"BB-z {comp.get('bollinger_z', 0):+.2f}  trend {'OK' if comp.get('trend_intact') else 'broken'}"
            )
    else:
        decisions_lines.append(f"\nBottom-catch: 0 oversold setups today (sleeve allocation cap is {sleeve_alloc.get('bottom', 0)*100:.0f}%; nothing deployed)")

    if approved_bottoms:
        decisions_lines.append(f"\nApproved by Bull/Bear/Risk debate: {len(approved_bottoms)}")
        for b in approved_bottoms:
            c = b["candidate"]
            decisions_lines.append(f"  {c.ticker}: {b['position_pct']*100:.1f}% allocation")

    sections.append(_section("DECISIONS", "\n".join(decisions_lines) or "no actionable signals today"))

    # ---- (4) SLEEVE WEIGHTS + ATTRIBUTION ----
    actual_mom_deployed = sum(v for k, v in final_targets.items() if k in {p.ticker for p in momentum_picks})
    actual_bot_deployed = sum(v for k, v in final_targets.items() if k not in {p.ticker for p in momentum_picks})
    sleeve_lines = [
        f"Cap:   momentum {sleeve_alloc.get('momentum', 0)*100:.0f}% / bottom-catch {sleeve_alloc.get('bottom', 0)*100:.0f}% (method: {sleeve_method})",
        f"Today: momentum {actual_mom_deployed*100:.1f}% deployed / bottom-catch {actual_bot_deployed*100:.1f}% deployed",
    ]
    if sleeve_pnl:
        sleeve_lines.append("\nSleeve P&L (cumulative since strategy start):")
        for sleeve in ("MOMENTUM", "BOTTOM_CATCH"):
            d = sleeve_pnl.get(sleeve, {})
            unrealized = d.get("unrealized_pl", 0)
            realized = d.get("realized_pl", 0)
            sleeve_lines.append(
                f"  {sleeve:14s}  realized {_fmt_money(realized, sign=True)}  unrealized {_fmt_money(unrealized, sign=True)}"
            )
    sections.append(_section("SLEEVE", "\n".join(sleeve_lines)))

    # ---- (5) RISK GATES ----
    risk_lines = []
    if vix is not None:
        risk_lines.append(f"VIX: {vix:.1f}")
    for w in (risk_warnings or []):
        risk_lines.append(f"  WARN: {w}")
    risk_lines.append(f"Final target gross: {sum(final_targets.values())*100:.1f}%")
    risk_lines.append(f"Number of positions targeted: {len(final_targets)}")
    if daily_vol:
        risk_lines.append(f"Realized 1σ daily vol (rolling): ±{daily_vol*100:.2f}%")
    sections.append(_section("RISK GATES", "\n".join(risk_lines)))

    # ---- (6) ORDERS — with rationale ----
    order_lines = []
    if rebalance_results:
        order_lines.append("Momentum rebalance:")
        for r in rebalance_results:
            order_lines.append(_explain_order(r))
    if bracket_results:
        order_lines.append("\nBottom-catch brackets:")
        for r in bracket_results:
            order_lines.append(f"  {r.get('symbol', '?')}: {r.get('status', '?')}  ({r.get('rationale', '')})")
    if not order_lines:
        order_lines = ["No orders this run (no changes from current allocation)."]
    sections.append(_section("ORDERS", "\n".join(order_lines)))

    # ---- (6.5) POSITION RETURN SINCE ENTRY — names diverging materially from SPY ----
    # v6.0.x rename: previously titled "ANOMALOUS MOVES (>2% vs SPY)" which
    # implied INTRADAY divergence. The underlying metric (unrealized_plpc)
    # is CUMULATIVE return since position entry — the previous label was
    # confusing on multi-day-old positions, and outright misleading after a
    # broker-resync (which resets entry → entry-to-now widens immediately).
    if positions_now and spy_today_return is not None:
        anomalous_lines = []
        spy_pct = spy_today_return
        for sym, p in positions_now.items():
            pos_pct = p.get("unrealized_plpc", 0)
            divergence = pos_pct - spy_pct
            if abs(divergence) > 0.02:  # >2% divergence from SPY's last-session move
                direction = "DOWN" if pos_pct < 0 else "UP"
                anomalous_lines.append(
                    f"  {sym}: {pos_pct*100:+.2f}% since entry  "
                    f"({direction} {abs(divergence)*100:.2f}% vs SPY's last session)"
                )
        if anomalous_lines:
            # Label varies based on whether today is a trading day —
            # on weekends the comparison is between cumulative position
            # return and Friday's SPY move, so even the "vs SPY" framing
            # needs care.
            section_title = (
                "POSITION RETURN SINCE ENTRY (>2% vs SPY last session)"
                if market_open_today else
                "POSITION RETURN SINCE ENTRY (>2% vs last-session SPY) — "
                "market closed today, figures are NOT intraday"
            )
            sections.append(_section(
                section_title,
                "\n".join(anomalous_lines) +
                "\n\nThese are CUMULATIVE returns since each lot was opened "
                "(not single-day moves). The LLM analysis below should "
                "explain the larger divergences via web search."
            ))

    # ---- (7) POSITIONS — with age + next decision date ----
    if positions_now:
        pos_lines = ["(next reconsidered at monthly rebalance: {} \u2014 in {} days)".format(next_rebal, days_to_rebal)]
        sorted_pos = sorted(positions_now.items(), key=lambda x: -x[1].get("market_value", 0))
        for sym, p in sorted_pos:
            mv = p.get("market_value", 0)
            pl = p.get("unrealized_pl", 0)
            plpc = p.get("unrealized_plpc", 0) * 100
            entry = p.get("avg_entry_price", 0)
            now_p = p.get("current_price", 0)
            age = p.get("age_days")
            age_str = f"  age {age}d" if age is not None else ""
            sleeve = p.get("sleeve") or ""
            pos_lines.append(
                f"  {sym:6s}  {_fmt_money(mv):>10s}  entry {_fmt_money(entry):>8s}  now {_fmt_money(now_p):>8s}  "
                f"P&L {_fmt_money(pl, sign=True):>9s}  ({plpc:+.2f}%){age_str}{('  '+sleeve) if sleeve else ''}"
            )
        sections.append(_section(f"POSITIONS ({len(positions_now)})", "\n".join(pos_lines)))

    # ---- (8) UPCOMING EVENTS ----
    if today_anomalies or future_anomalies:
        upc_lines = []
        if today_anomalies:
            upc_lines.append("TODAY:")
            for a in today_anomalies:
                upc_lines.append(
                    f"  {a.name} [{a.confidence}]  +{a.expected_alpha_bps}bps expected  →  {a.target_symbol}\n"
                    f"    {a.rationale}"
                )
        if future_anomalies:
            upc_lines.append("\nUPCOMING (next 7 days):")
            seen = set()
            for d, a in future_anomalies:  # already in date order
                if a.name in seen:
                    continue  # only show soonest occurrence per anomaly type
                seen.add(a.name)
                days_until = (d - today).days
                when = "tomorrow" if days_until == 1 else f"in {days_until}d ({d})"
                upc_lines.append(
                    f"  {a.name} {when}  [{a.confidence}]  +{a.expected_alpha_bps}bps expected"
                )
        sections.append(_section("UPCOMING EVENTS", "\n".join(upc_lines)))

    # ---- (9) ANALYSIS (LLM) ----
    try:
        from .narrative import generate_narrative
        narrative_state = {
            "run_id": run_id,
            "account": {
                "equity": equity_after, "cash": cash_after, "deployed": deployed,
                "day_pnl": day_pnl, "day_pct": day_pct,
                "cum_pnl": cum_pnl, "cum_pct": cum_pct,
                "yesterday_equity": yesterday_equity,
                "drawdown_from_peak": dd_from_peak,
            },
            "market": {"spy_today_return": spy_today_return, "vix": vix,
                       "excess_today": excess_return,
                       "jensen_alpha_annualized": (beta_alpha or {}).get("alpha_annualized")},
            "decisions": {
                "momentum_picks": [
                    {"ticker": c.ticker, "trailing_return": c.rationale.get("trailing_return", 0),
                     "atr_pct": c.atr_pct} for c in momentum_picks
                ],
                "bottom_candidates_count": len(bottom_candidates),
            },
            "sleeve_alloc": {**sleeve_alloc, "method": sleeve_method,
                             "actual_momentum_deployed": actual_mom_deployed,
                             "actual_bottom_deployed": actual_bot_deployed},
            "risk_warnings": risk_warnings,
            "orders": rebalance_results + bracket_results,
            "positions": positions_now or {},
            "anomalies_today": today_anomalies,
            "upcoming_events": [(str(d), a.name, a.confidence, a.expected_alpha_bps) for d, a in future_anomalies],
            "next_monthly_rebalance": str(next_rebal),
            "days_to_rebalance": days_to_rebal,
        }
        narrative_text = generate_narrative(narrative_state)
        if narrative_text:
            sections.append(_section("ANALYSIS (LLM)", narrative_text))
    except Exception as e:
        sections.append(_section("ANALYSIS (LLM)", f"(narrative unavailable: {type(e).__name__}: {e})"))

    # ---- (10) META ----
    sections.append(_section(
        "META",
        f"Run ID: {run_id}\n"
        f"Generated: {_now_pt()}\n"
        f"Strategy version: {STRATEGY_VERSION}\n"
        f"Realistic expected CAGR (post-DSR correction): 10-12%\n"
        f"Last walk-forward OOS Sharpe: 0.76\n"
        f"Next monthly rebalance: {next_rebal}\n"
        f"Repo: https://github.com/rca6045407168/trader"
    ))

    body = "\n".join(sections)
    return subject, body


def fetch_alpaca_position_dicts(client) -> dict[str, dict]:
    """Pull current Alpaca positions and merge with journal lot data (sleeve, age)."""
    from .journal import _conn
    out = {}
    for p in client.get_all_positions():
        out[p.symbol] = {
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "qty": float(p.qty),
        }
    # Enrich with lot metadata
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT symbol, sleeve, opened_at FROM position_lots
                   WHERE closed_at IS NULL"""
            ).fetchall()
        for r in rows:
            if r["symbol"] in out:
                opened = pd.Timestamp(r["opened_at"])
                age = (pd.Timestamp.now() - opened).days
                out[r["symbol"]]["sleeve"] = r["sleeve"]
                out[r["symbol"]]["age_days"] = max(age, 0)
    except Exception:
        pass
    return out


def fetch_spy_today_return() -> float | None:
    try:
        from .data import fetch_history
        spy = fetch_history(["SPY"], start=(datetime.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))["SPY"]
        if len(spy) < 2:
            return None
        return float(spy.iloc[-1] / spy.iloc[-2] - 1)
    except Exception:
        return None


def fetch_yesterday_equity() -> float | None:
    from .journal import recent_snapshots
    snaps = recent_snapshots(days=7)
    today_iso = datetime.utcnow().date().isoformat()
    for s in snaps:
        if s["date"] != today_iso:
            return float(s["equity"]) if s["equity"] else None
    return None


def fetch_recent_snapshots(days: int = 30) -> list[dict]:
    from .journal import recent_snapshots
    return recent_snapshots(days=days) or []


def fetch_sleeve_pnl(positions_now: dict[str, dict]) -> dict[str, dict]:
    """Sleeve-level P&L summary from journal lots + current unrealized."""
    from .journal import _conn
    out = {"MOMENTUM": {"realized_pl": 0.0, "unrealized_pl": 0.0},
           "BOTTOM_CATCH": {"realized_pl": 0.0, "unrealized_pl": 0.0}}
    try:
        with _conn() as c:
            # Realized
            rows = c.execute(
                """SELECT sleeve, SUM(realized_pnl) as total
                   FROM position_lots WHERE closed_at IS NOT NULL
                   GROUP BY sleeve"""
            ).fetchall()
            for r in rows:
                if r["sleeve"] in out:
                    out[r["sleeve"]]["realized_pl"] = float(r["total"] or 0)
            # Unrealized — split by sleeve via lots
            lot_rows = c.execute(
                """SELECT symbol, sleeve, qty, open_price FROM position_lots
                   WHERE closed_at IS NULL"""
            ).fetchall()
            for lot in lot_rows:
                sym = lot["symbol"]
                if sym not in positions_now:
                    continue
                current_price = positions_now[sym].get("current_price", 0)
                lot_unrealized = (current_price - (lot["open_price"] or 0)) * (lot["qty"] or 0)
                if lot["sleeve"] in out:
                    out[lot["sleeve"]]["unrealized_pl"] += lot_unrealized
    except Exception:
        pass
    return out
