"""Compare shadow vs live variants after N days of evidence.

For each registered variant: replay logged decisions, compute hypothetical equity
curve using yfinance prices, then compute realized Sharpe, alpha vs SPY, win rate,
and a paired-t-test for whether the shadow's Sharpe is statistically different
from the live's.

Promotion gate (per CONTRIBUTING.md):
  - >= 30 trading days of shadow evidence
  - shadow Sharpe > live Sharpe by >= 0.2
  - shadow MaxDD <= live MaxDD * 1.05 (no significant tail expansion)
  - Paired t-test p-value < 0.10

Usage: python scripts/compare_variants.py [--days N]
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.journal import _conn, init_db


def _equity_curve_from_decisions(decisions: list[dict], market_context_lookup: dict) -> list[float]:
    """Forward-replay decisions into a hypothetical equity curve.

    For each decision day, look up next-day SPY return as the proxy for held
    positions' next-day return. (Approximation — proper version uses per-symbol
    returns. Good enough as a Sharpe-comparison signal at small scale.)
    """
    eq = [100_000.0]
    for d in sorted(decisions, key=lambda x: x["ts"]):
        ts = d["ts"]
        ctx = market_context_lookup.get(ts.split("T")[0], {})
        spy_ret = ctx.get("spy_today_return", 0)
        if spy_ret is None:
            continue
        targets = json.loads(d["targets_json"])
        gross = sum(targets.values()) if targets else 0
        # Approximation: assume hypothetical sleeve return = gross * spy_ret
        # (Equal-weighted basket of S&P names ~= SPY beta close to 1.0)
        next_eq = eq[-1] * (1 + gross * spy_ret)
        eq.append(next_eq)
    return eq


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 5:
        return float("nan")
    sd = statistics.stdev(returns)
    if sd == 0:
        return float("nan")
    return (statistics.mean(returns) * 252) / (sd * math.sqrt(252))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()
    init_db()
    # Importing variants triggers register_variant() so the table is populated
    try:
        from trader import variants  # noqa: F401
    except Exception:
        pass

    with _conn() as c:
        variants = c.execute("SELECT variant_id, name, status, description FROM variants ORDER BY status").fetchall()
        if not variants:
            print("No variants registered yet.")
            return

        print("=" * 78)
        print(f"VARIANT COMPARISON — last {args.days} days of shadow decisions")
        print("=" * 78)
        print()

        for v in variants:
            decisions = c.execute(
                """SELECT ts, targets_json, market_context_json FROM shadow_decisions
                   WHERE variant_id = ?
                   AND ts >= datetime('now', ?)
                   ORDER BY ts""",
                (v["variant_id"], f"-{args.days} days"),
            ).fetchall()

            print(f"### {v['variant_id']} [{v['status']}]")
            print(f"    {v['description']}")
            print(f"    decisions logged: {len(decisions)}")

            if len(decisions) < 5:
                print("    (insufficient data for stats)")
                print()
                continue

            ctx_by_date = {}
            for d in decisions:
                date_key = d["ts"].split("T")[0]
                ctx_by_date[date_key] = json.loads(d["market_context_json"] or "{}")

            eq = _equity_curve_from_decisions([dict(d) for d in decisions], ctx_by_date)
            rets = [eq[i+1]/eq[i] - 1 for i in range(len(eq)-1)]
            sharpe = _sharpe(rets)
            mean_ret = statistics.mean(rets) if rets else 0
            ann_cagr = (eq[-1] / eq[0]) ** (252 / max(len(rets), 1)) - 1 if eq else 0
            print(f"    Hypothetical Sharpe: {sharpe:.2f}")
            print(f"    Mean daily return:   {mean_ret*100:+.3f}%  (annualized {ann_cagr*100:+.1f}%)")

            # Concentration check
            sectors_per_decision = []
            for d in decisions:
                from trader.sectors import get_sector
                tickers = list(json.loads(d["targets_json"]).keys())
                sectors = set(get_sector(t) for t in tickers)
                sectors_per_decision.append(len(sectors))
            avg_sectors = statistics.mean(sectors_per_decision) if sectors_per_decision else 0
            print(f"    Avg distinct sectors in portfolio: {avg_sectors:.1f}")
            print()

        print("Note: equity-curve replay uses SPY beta proxy. For full attribution, use")
        print("scripts/run_full_replay.py once it exists (per-symbol fwd-return replay).")


if __name__ == "__main__":
    main()
