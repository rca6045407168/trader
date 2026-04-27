"""Detailed daily report assembly.

Builds the email body for a daily-run completion email. Aimed at telling
Richard, in one scroll, everything he'd want to know:
  - what the system decided and why
  - what risk gates fired
  - what orders went out
  - what positions he now holds + P&L
  - how he's tracking vs SPY
  - what anomalies are firing today (advisory)
  - what to watch for tomorrow

Structured as plain text so it renders cleanly in any email client.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any
import pandas as pd


def _section(title: str, body: str) -> str:
    return f"=== {title} ===\n{body}\n"


def _fmt_pct(x: float, plus: bool = True) -> str:
    sign = "+" if plus and x >= 0 else ""
    return f"{sign}{x * 100:.2f}%"


def _fmt_money(x: float, sign: bool = False) -> str:
    if sign:
        return f"${x:+,.2f}"
    return f"${x:,.2f}"


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
) -> tuple[str, str]:
    """Returns (subject, body)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip()

    # ---- HEADER (1 line summary used as subject seed) ----
    day_pnl = equity_after - (yesterday_equity or 100_000)
    day_pct = day_pnl / (yesterday_equity or 100_000) if yesterday_equity else 0
    cum_pnl = equity_after - 100_000
    cum_pct = cum_pnl / 100_000

    spy_str = f"SPY {_fmt_pct(spy_today_return)}" if spy_today_return is not None else "SPY n/a"
    alpha_str = ""
    if spy_today_return is not None and yesterday_equity:
        alpha = day_pct - spy_today_return
        alpha_str = f" alpha {_fmt_pct(alpha)}"

    subject = f"day {_fmt_pct(day_pct)}{alpha_str} | equity {_fmt_money(equity_after)}"

    # ---- BODY SECTIONS ----
    sections = []

    # 1. Account snapshot
    deployed = equity_after - cash_after
    deployed_pct = deployed / equity_after if equity_after else 0
    sections.append(_section(
        "ACCOUNT",
        f"Equity:    {_fmt_money(equity_after)}\n"
        f"Cash:      {_fmt_money(cash_after)}  ({(cash_after/equity_after*100 if equity_after else 0):.0f}%)\n"
        f"Deployed:  {_fmt_money(deployed)}  ({deployed_pct*100:.0f}%)\n"
        f"Day P&L:   {_fmt_money(day_pnl, sign=True)}  ({_fmt_pct(day_pct)})  vs {spy_str}{alpha_str}\n"
        f"Total P&L: {_fmt_money(cum_pnl, sign=True)}  ({_fmt_pct(cum_pct)}) since $100k start"
    ))

    # 2. Decisions made today
    decisions_lines = []
    if momentum_picks:
        decisions_lines.append(f"Momentum sleeve picked top {len(momentum_picks)} by 12-month trailing return:")
        for c in momentum_picks:
            ret = c.rationale.get("trailing_return", 0)
            decisions_lines.append(
                f"  {c.ticker:6s}  12m return {_fmt_pct(ret):>8s}  ATR {c.atr_pct*100:.1f}%"
            )
    if bottom_candidates:
        decisions_lines.append(f"\nBottom-catch scan: {len(bottom_candidates)} candidates passed threshold (score >= 0.65):")
        for c in bottom_candidates[:5]:
            comp = c.rationale
            decisions_lines.append(
                f"  {c.ticker:6s}  score {c.score:.2f}  RSI {comp.get('rsi', 0):.0f}  "
                f"BB-z {comp.get('bollinger_z', 0):+.2f}  trend {'OK' if comp.get('trend_intact') else 'broken'}"
            )
    else:
        decisions_lines.append("\nBottom-catch scan: 0 candidates today (no oversold setup met threshold).")

    if approved_bottoms:
        decisions_lines.append(f"\nApproved by Bull/Bear/Risk debate: {len(approved_bottoms)}")
        for b in approved_bottoms:
            c = b["candidate"]
            decisions_lines.append(f"  {c.ticker}: {b['position_pct']*100:.1f}% allocation")

    sections.append(_section("DECISIONS", "\n".join(decisions_lines) or "no actionable signals today"))

    # 3. Sleeve allocation math
    sections.append(_section(
        "SLEEVE WEIGHTS",
        f"Momentum:     {sleeve_alloc.get('momentum', 0)*100:.0f}%\n"
        f"Bottom-catch: {sleeve_alloc.get('bottom', 0)*100:.0f}%\n"
        f"Method:       {sleeve_method}  (uses backtest priors until 6+ months of live data accumulate)"
    ))

    # 4. Risk gates
    risk_lines = []
    if vix is not None:
        risk_lines.append(f"VIX: {vix:.1f}")
    if risk_warnings:
        for w in risk_warnings:
            risk_lines.append(f"  WARN: {w}")
    risk_lines.append(f"Final target gross: {sum(final_targets.values())*100:.1f}%")
    risk_lines.append(f"Number of positions targeted: {len(final_targets)}")
    sections.append(_section("RISK GATES", "\n".join(risk_lines)))

    # 5. Orders submitted
    order_lines = []
    if rebalance_results:
        order_lines.append("Momentum rebalance:")
        for r in rebalance_results:
            sym = r.get("symbol", "?")
            side = r.get("side", "")
            notional = r.get("notional", 0)
            status = r.get("status", "")
            err = r.get("error")
            line = f"  {sym:6s}  {side:6s}  {_fmt_money(notional)}  {status}"
            if err:
                line += f"  ERROR: {err}"
            order_lines.append(line)
    if bracket_results:
        order_lines.append("\nBottom-catch brackets:")
        for r in bracket_results:
            order_lines.append(f"  {r.get('symbol', '?')}: {r.get('status', '?')}  ({r.get('rationale', '')})")
    if not order_lines:
        order_lines = ["No orders this run (no changes from current allocation)."]
    sections.append(_section("ORDERS", "\n".join(order_lines)))

    # 6. Positions
    if positions_now:
        pos_lines = []
        sorted_pos = sorted(positions_now.items(), key=lambda x: -x[1].get("market_value", 0))
        for sym, p in sorted_pos:
            mv = p.get("market_value", 0)
            pl = p.get("unrealized_pl", 0)
            plpc = p.get("unrealized_plpc", 0) * 100
            entry = p.get("avg_entry_price", 0)
            now_p = p.get("current_price", 0)
            pos_lines.append(
                f"  {sym:6s}  {_fmt_money(mv):>10s}  entry {_fmt_money(entry):>8s}  now {_fmt_money(now_p):>8s}  "
                f"P&L {_fmt_money(pl, sign=True):>9s}  ({plpc:+.2f}%)"
            )
        sections.append(_section(f"POSITIONS ({len(positions_now)})", "\n".join(pos_lines)))

    # 7. Narrative analysis (Claude-generated) — WHY decisions, short/long-term factors
    try:
        from .narrative import generate_narrative
        narrative_state = {
            "run_id": run_id,
            "account": {
                "equity": equity_after,
                "cash": cash_after,
                "deployed": deployed,
                "day_pnl": day_pnl,
                "day_pct": day_pct,
                "cum_pnl": cum_pnl,
                "cum_pct": cum_pct,
                "yesterday_equity": yesterday_equity,
            },
            "market": {"spy_today_return": spy_today_return, "vix": vix},
            "decisions": {
                "momentum_picks": [
                    {
                        "ticker": c.ticker,
                        "trailing_return": c.rationale.get("trailing_return", 0),
                        "atr_pct": c.atr_pct,
                    } for c in momentum_picks
                ],
                "bottom_candidates_count": len(bottom_candidates),
            },
            "sleeve_alloc": {**sleeve_alloc, "method": sleeve_method},
            "risk_warnings": risk_warnings,
            "orders": rebalance_results + bracket_results,
            "positions": positions_now or {},
            "anomalies_today": anomalies_today or [],
        }
        narrative_text = generate_narrative(narrative_state)
        if narrative_text:
            sections.append(_section("ANALYSIS (LLM-generated)", narrative_text))
    except Exception as e:
        sections.append(_section("ANALYSIS (LLM-generated)", f"(narrative unavailable: {type(e).__name__})"))

    # 8. Anomalies on the radar today
    if anomalies_today:
        anom_lines = []
        for a in anomalies_today:
            anom_lines.append(
                f"  {a.name} [{a.confidence}]  +{a.expected_alpha_bps}bps expected  →  {a.target_symbol}\n"
                f"    {a.rationale}"
            )
        sections.append(_section("ANOMALIES FIRING TODAY (advisory, not auto-traded)", "\n".join(anom_lines)))

    # 8. Footer
    sections.append(_section(
        "META",
        f"Run ID: {run_id}\n"
        f"Generated: {timestamp}\n"
        f"Strategy version: v2.2\n"
        f"Realistic expected CAGR (post-DSR): 10-12%\n"
        f"Last walk-forward OOS Sharpe: 0.76\n"
        f"Repo: https://github.com/rca6045407168/trader"
    ))

    body = "\n".join(sections)
    return subject, body


def fetch_alpaca_position_dicts(client) -> dict[str, dict]:
    """Helper: pull current positions and convert to plain dicts for the report."""
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
    return out


def fetch_spy_today_return() -> float | None:
    """Helper: today's SPY close-to-close return (nullable on data failure)."""
    try:
        from .data import fetch_history
        spy = fetch_history(["SPY"], start=(datetime.now() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))["SPY"]
        if len(spy) < 2:
            return None
        return float(spy.iloc[-1] / spy.iloc[-2] - 1)
    except Exception:
        return None


def fetch_yesterday_equity() -> float | None:
    """Last daily snapshot equity (excluding today's)."""
    from .journal import recent_snapshots
    snaps = recent_snapshots(days=7)
    today_iso = datetime.utcnow().date().isoformat()
    for s in snaps:
        if s["date"] != today_iso:
            return float(s["equity"]) if s["equity"] else None
    return None
