"""Weekly digest. Runs Sunday evening; emails a summary of the past week.

Reports:
  - Equity start-of-week / end-of-week / week return
  - vs SPY (with true beta/alpha if enough data)
  - Sleeve attribution
  - Trade activity (rotations, bottom-catches)
  - Rolling Sharpe (1m, 3m if available)
  - Drawdown from peak + days underwater
  - Upcoming week's events (FOMC, OPEX, earnings if known)
  - LLM commentary on what happened + what to watch
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math
import statistics
from datetime import datetime, date, timedelta

from trader.notify import notify
from trader.journal import recent_snapshots, _conn
from trader.data import fetch_history
from trader.perf_metrics import (
    fetch_portfolio_and_spy_returns,
    compute_beta_alpha,
    compute_drawdown_stats,
)
from trader.anomalies import scan_anomalies


def main():
    snaps = recent_snapshots(days=14)
    if len(snaps) < 2:
        # Not enough data for a meaningful weekly digest yet
        print("Insufficient snapshots for weekly digest; skipping.")
        return

    snaps = sorted(snaps, key=lambda s: s["date"])
    week_snaps = [s for s in snaps if s.get("equity")]
    if len(week_snaps) < 2:
        print("Insufficient equity data; skipping.")
        return

    eq_start = week_snaps[0]["equity"]
    eq_end = week_snaps[-1]["equity"]
    week_pnl = eq_end - eq_start
    week_pct = week_pnl / eq_start if eq_start else 0
    eqs = [s["equity"] for s in week_snaps]
    dd_stats = compute_drawdown_stats(eqs)

    # SPY week return
    spy_week = None
    try:
        from datetime import datetime as dt
        spy = fetch_history(["SPY"], start=(dt.now() - timedelta(days=14)).strftime("%Y-%m-%d"))["SPY"]
        if len(spy) >= 5:
            spy_week = float(spy.iloc[-1] / spy.iloc[-5] - 1)
    except Exception:
        pass

    # Beta / true alpha
    p_rets, s_rets = fetch_portfolio_and_spy_returns(days=30)
    beta_info = None
    if len(p_rets) >= 5 and len(s_rets) >= 5:
        beta_info = compute_beta_alpha(p_rets, s_rets)

    # Sleeve P&L from lots
    sleeve_pnl = {"MOMENTUM": {"realized": 0.0, "unrealized": 0.0},
                  "BOTTOM_CATCH": {"realized": 0.0, "unrealized": 0.0}}
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT sleeve, SUM(realized_pnl) as r FROM position_lots
                   WHERE closed_at IS NOT NULL GROUP BY sleeve"""
            ).fetchall()
            for r in rows:
                if r["sleeve"] in sleeve_pnl:
                    sleeve_pnl[r["sleeve"]]["realized"] = float(r["r"] or 0)
    except Exception:
        pass

    # Rolling Sharpe
    rolling_sharpe = None
    if len(p_rets) >= 10:
        mean_r = statistics.mean(p_rets)
        sd_r = statistics.stdev(p_rets) if len(p_rets) > 1 else 0
        if sd_r > 0:
            rolling_sharpe = (mean_r * 252) / (sd_r * math.sqrt(252))

    # Trade count this week
    trade_count = 0
    bottom_count = 0
    try:
        with _conn() as c:
            trade_count = c.execute(
                """SELECT COUNT(*) FROM orders
                   WHERE date(ts) >= date('now', '-7 days') AND status = 'submitted'"""
            ).fetchone()[0]
            bottom_count = c.execute(
                """SELECT COUNT(*) FROM decisions
                   WHERE date(ts) >= date('now', '-7 days') AND style = 'BOTTOM_CATCH'"""
            ).fetchone()[0]
    except Exception:
        pass

    # Upcoming week's events
    today = date.today()
    upcoming = []
    seen_names = set()
    for offset in range(8):
        for a in scan_anomalies(today + timedelta(days=offset)):
            if a.name in seen_names:
                continue
            seen_names.add(a.name)
            upcoming.append((today + timedelta(days=offset), a))

    # Build body
    lines = [
        "=== WEEKLY DIGEST ===",
        f"Period: {week_snaps[0]['date']} to {week_snaps[-1]['date']} ({len(week_snaps)} trading days)",
        "",
        "PERFORMANCE",
        f"  Equity start:  ${eq_start:,.2f}",
        f"  Equity end:    ${eq_end:,.2f}",
        f"  Week P&L:      ${week_pnl:+,.2f}  ({week_pct*100:+.2f}%)",
    ]
    if spy_week is not None:
        excess = week_pct - spy_week
        lines.append(f"  SPY week:      {spy_week*100:+.2f}%   excess {excess*100:+.2f}%")
    lines += [
        f"  Drawdown:      {dd_stats['current_dd']*100:+.2f}% from ATH ${dd_stats['all_time_high']:,.2f}",
        f"  Days underwater: {dd_stats['days_underwater']}",
    ]
    if beta_info and not math.isnan(beta_info.get("beta", float("nan"))):
        lines.append(
            f"  True alpha (n={beta_info['n_obs']}): β={beta_info['beta']:.2f}, "
            f"α={beta_info['alpha_annualized']*100:+.2f}%/yr, "
            f"R²={beta_info['r_squared']:.2f}"
        )
    if rolling_sharpe is not None:
        lines.append(f"  Rolling Sharpe (~{len(p_rets)}d): {rolling_sharpe:.2f}")

    lines += [
        "",
        "SLEEVE ATTRIBUTION",
        f"  MOMENTUM:     realized ${sleeve_pnl['MOMENTUM']['realized']:+.2f}",
        f"  BOTTOM_CATCH: realized ${sleeve_pnl['BOTTOM_CATCH']['realized']:+.2f}",
        "",
        "ACTIVITY",
        f"  Orders submitted this week: {trade_count}",
        f"  Bottom-catch decisions:     {bottom_count}",
        "",
        "UPCOMING NEXT WEEK",
    ]
    if upcoming:
        for d, a in upcoming[:8]:
            days_until = (d - today).days
            when = "today" if days_until == 0 else f"in {days_until}d ({d})"
            lines.append(f"  {a.name} {when} [{a.confidence}] +{a.expected_alpha_bps}bps expected")
    else:
        lines.append("  No documented anomalies firing in the next 7 days.")

    lines += [
        "",
        "META",
        f"  Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"  Strategy: v2.7  /  Realistic CAGR target: 10-12%",
        f"  Repo: https://github.com/rca6045407168/trader",
    ]

    body = "\n".join(lines)
    subject = f"Weekly: {week_pct*100:+.2f}%"
    if spy_week is not None:
        subject += f" vs SPY {spy_week*100:+.2f}%"
    subject += f" | DD {dd_stats['current_dd']*100:+.2f}%"

    notify(body, subject=subject, level="info")


if __name__ == "__main__":
    main()
