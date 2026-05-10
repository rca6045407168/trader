#!/usr/bin/env python3
"""Historical TLH simulator.

Answers: "what realized loss would the TLH planner have harvested
on a cap-weighted basket of the 50 names between dates X and Y?"

Mechanics:
  - At start_date, allocate `starting_capital` cap-weighted (using
    APPROX_CAP_B) across the 50-name universe. Cost basis = open
    price on that day.
  - For each subsequent trading day:
      For each open position:
        * Compute unrealized P&L.
        * If unrealized loss >= min_loss_pct:
            - Walk REPLACEMENT_MAP[sym] in order. First candidate
              that's NOT in a wash-sale window AND not currently
              held becomes the replacement.
            - Execute swap: sell sym at today's close, buy
              replacement at today's close. Record realized loss.
            - sym gets stamped in last_sold[]; cannot re-buy for
              31 days.
  - Returns aggregate realized loss + per-event detail.

Edge model used in calculating "tax saved":
  - Combined marginal rate (federal + state) applied to the
    absolute value of realized losses. The IRS waterfall (cap
    gains → $3k ordinary → carry-forward) is NOT modeled here —
    the report just shows the gross tax shelter generated.
  - For a multi-year backtest with NO offsetting capital gains,
    the $3k/yr cap matters and a portion would carry forward.
    The "tax saved" number is therefore a CEILING for the
    immediate-year case; for a long horizon it's a reasonable
    estimate because carry-forwards eventually get used.

Usage:
  python scripts/tlh_backtest.py --start 2026-04-23 --end 2026-05-06
  python scripts/tlh_backtest.py --start 2025-05-06 --end 2026-05-06
  python scripts/tlh_backtest.py --start 2021-05-06 --end 2026-05-06
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trader.direct_index_tlh import (  # noqa: E402
    REPLACEMENT_MAP, APPROX_CAP_B, cap_weighted_targets,
    quality_tilted_targets, WASH_SALE_DAYS,
)


def fetch_universe_prices(tickers: list[str],
                           start: str,
                           end: str) -> pd.DataFrame:
    """Pull adjusted close history for tickers. Returns wide DataFrame
    (date index, ticker columns). Cached one fetch per process."""
    import yfinance as yf
    # yfinance balks on tickers with hyphens (BRK-B). Convert to dot form.
    yf_tickers = [t.replace("-", "-") for t in tickers]  # noop but explicit
    df = yf.download(yf_tickers, start=start, end=end,
                      progress=False, auto_adjust=True, threads=True)
    # df.columns is a MultiIndex (field, ticker); we want adjusted close
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            df = df["Close"]
        else:
            df = df.xs(df.columns.get_level_values(0)[0], axis=1, level=0)
    return df


def simulate(prices: pd.DataFrame,
              start_date: str,
              end_date: str,
              starting_capital: float = 100_000.0,
              monthly_contribution: float = 0.0,
              min_loss_pct: float = 0.05,
              wash_sale_days: int = WASH_SALE_DAYS,
              quality_tilt: float = 0.0) -> dict:
    """Multi-lot HIFO simulator. Matches production planner mechanics.

    Per-name positions are tracked as a *list of lots* (qty, cost_basis,
    opened_at). At each daily check, we scan lots highest-cost-first
    (HIFO) and harvest the most expensive lot that's >= min_loss_pct
    underwater. Replacement is sector-matched per REPLACEMENT_MAP with
    31-day wash-sale enforcement.

    `monthly_contribution`: realistic DCA pattern. Every ~21 trading
    days, contribute this amount cap-weighted across the universe.
    Each contribution creates fresh lots at that day's price — this
    is the structural mechanism by which TLH harvests work in
    long-running production accounts. With contribution=0, the
    simulator degenerates to the single-lot buy-and-hold case (which
    in a bull market yields ~zero harvests).
    """
    universe = [t for t in REPLACEMENT_MAP.keys() if t in prices.columns]
    if not universe:
        return {"error": "no overlap between universe and prices.columns"}

    px = prices.loc[start_date:end_date].dropna(how="all")
    if px.empty:
        return {"error": "no price rows in window"}

    if quality_tilt > 0:
        weights = quality_tilted_targets(universe, gross=1.0,
                                           tilt_strength=quality_tilt)
    else:
        weights = cap_weighted_targets(universe, gross=1.0)
    # positions[sym] = list of {qty, cost_basis, opened_at}
    positions: dict[str, list[dict]] = {sym: [] for sym in universe}
    skipped_init = []
    total_contributed = 0.0

    # Initial cap-weighted purchase at start
    initial = px.iloc[0]
    for t, w in weights.items():
        if t in initial.index and not pd.isna(initial.get(t)):
            p0 = float(initial[t])
            if p0 > 0:
                dollars = w * starting_capital
                positions[t].append({
                    "qty": dollars / p0,
                    "cost_basis": p0,
                    "opened_at": px.index[0],
                })
        else:
            skipped_init.append(t)
    total_contributed += starting_capital

    last_sold: dict[str, pd.Timestamp] = {}
    swaps: list[dict] = []
    realized_loss = 0.0
    contribution_dates: list = []
    days_since_contribution = 0

    for date_idx in range(1, len(px)):
        date = px.index[date_idx]
        days_since_contribution += 1

        # Monthly contribution (~21 trading days = 1 month)
        if monthly_contribution > 0 and days_since_contribution >= 21:
            for t, w in weights.items():
                try:
                    p_now = float(px.iloc[date_idx][t])
                except (KeyError, TypeError):
                    continue
                if pd.isna(p_now) or p_now <= 0:
                    continue
                positions[t].append({
                    "qty": w * monthly_contribution / p_now,
                    "cost_basis": p_now,
                    "opened_at": date,
                })
            total_contributed += monthly_contribution
            contribution_dates.append(date)
            days_since_contribution = 0

        # Harvest scan — HIFO across each ticker's lot list
        for sym in list(positions.keys()):
            if not positions[sym]:
                continue
            try:
                cur_px = float(px.iloc[date_idx][sym])
            except (KeyError, TypeError):
                continue
            if pd.isna(cur_px):
                continue

            # HIFO: most expensive lot first
            sorted_lots = sorted(positions[sym],
                                  key=lambda l: -l["cost_basis"])
            harvest_lot = None
            for lot in sorted_lots:
                if lot["cost_basis"] <= 0:
                    continue
                # Don't harvest a lot bought today (no actual loss path)
                if (date - lot["opened_at"]).days < 1:
                    continue
                unrealized_pct = (cur_px - lot["cost_basis"]) / lot["cost_basis"]
                if unrealized_pct <= -min_loss_pct:
                    harvest_lot = lot
                    harvest_pct = unrealized_pct
                    break
            if harvest_lot is None:
                continue

            # Find a wash-sale-safe sector-matched replacement
            candidates = REPLACEMENT_MAP.get(sym, [])
            replacement = None
            for c in candidates:
                if c in last_sold and (date - last_sold[c]).days <= wash_sale_days:
                    continue
                if c not in px.columns:
                    continue
                try:
                    cpx = float(px.iloc[date_idx][c])
                except (KeyError, TypeError):
                    continue
                if pd.isna(cpx) or cpx <= 0:
                    continue
                replacement = c
                break
            if replacement is None:
                continue

            # Execute the swap at today's close — sell harvest_lot only,
            # leave other lots intact
            proceeds = harvest_lot["qty"] * cur_px
            cost = harvest_lot["qty"] * harvest_lot["cost_basis"]
            this_loss = proceeds - cost  # negative
            realized_loss += this_loss

            new_px = float(px.iloc[date_idx][replacement])
            positions[replacement].append({
                "qty": proceeds / new_px,
                "cost_basis": new_px,
                "opened_at": date,
            })
            positions[sym].remove(harvest_lot)
            last_sold[sym] = date

            swaps.append({
                "date": date,
                "sell": sym,
                "buy": replacement,
                "qty": harvest_lot["qty"],
                "sell_price": cur_px,
                "cost_basis": harvest_lot["cost_basis"],
                "loss": this_loss,
                "loss_pct": harvest_pct,
            })

    # Final equity: mark all lots to market
    last_row = px.iloc[-1]
    final_equity = 0.0
    for sym, lots in positions.items():
        if not lots:
            continue
        try:
            mark = float(last_row[sym])
            if pd.isna(mark):
                continue
        except Exception:
            continue
        for lot in lots:
            final_equity += lot["qty"] * mark

    return {
        "start": str(px.index[0].date()),
        "end": str(px.index[-1].date()),
        "trading_days": len(px),
        "n_holdings": len(universe),
        "skipped_init": skipped_init,
        "starting_capital": starting_capital,
        "monthly_contribution": monthly_contribution,
        "total_contributed": total_contributed,
        "n_contributions": len(contribution_dates),
        "final_basket_value": final_equity,
        # Total return relative to total deposits (vs single-shot)
        "basket_return_pct": (final_equity - total_contributed) / total_contributed,
        "realized_loss": realized_loss,
        "n_swaps": len(swaps),
        "swaps": swaps,
    }


def render_summary(result: dict, label: str, combined_tax_rate: float) -> str:
    if "error" in result:
        return f"[{label}] ERROR: {result['error']}"
    rl = result["realized_loss"]
    tax_saved = abs(rl) * combined_tax_rate
    base = result.get("total_contributed", result["starting_capital"])
    uplift = tax_saved / base
    yrs = result["trading_days"] / 252
    lines = [
        f"=== {label} ===",
        f"  Window:            {result['start']}  →  {result['end']}"
        f"  ({result['trading_days']} trading days, ~{yrs:.2f} yr)",
        f"  Starting capital:  ${result['starting_capital']:>14,.2f}",
    ]
    if result.get("monthly_contribution", 0) > 0:
        lines.append(
            f"  DCA contribution:  ${result['monthly_contribution']:>14,.2f}/mo "
            f"× {result['n_contributions']} = "
            f"${result['monthly_contribution']*result['n_contributions']:,.2f} "
            f"(total deposits: ${result['total_contributed']:,.2f})"
        )
    lines += [
        f"  Final basket:      ${result['final_basket_value']:>14,.2f}"
        f"  ({result['basket_return_pct']*100:+.2f}% vs deposits)",
        f"  Swaps executed:    {result['n_swaps']:>14}",
        f"  Realized loss:     ${rl:>14,.2f}",
        f"  ★ Tax saved @ {combined_tax_rate*100:.0f}%: "
        f"${tax_saved:>14,.2f}  ({uplift*100:+.3f}% of capital)",
    ]
    if yrs > 0:
        lines.append(f"  Annualized uplift: ~{uplift/yrs*100:.2f}%/yr")
    if result["skipped_init"]:
        lines.append(f"  (init skipped: {', '.join(result['skipped_init'])})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--monthly", type=float, default=0.0,
                     help="Monthly DCA contribution $ (default 0). "
                          "Real-world TLH requires contributions OR "
                          "rebalancing to create fresh-cost-basis lots.")
    ap.add_argument("--tax-rate", type=float, default=0.37,
                     help="Combined federal+state marginal (default 0.37 = 32+5)")
    ap.add_argument("--min-loss-pct", type=float, default=0.05)
    ap.add_argument("--quality-tilt", type=float, default=0.0,
                     help="Novy-Marx quality tilt 0..1 (default 0). "
                          "0.5 = moderate tilt toward high-quality.")
    ap.add_argument("--label", default="TLH backtest")
    ap.add_argument("--show-swaps", type=int, default=0,
                     help="Show top N swaps by loss size")
    args = ap.parse_args(argv)

    universe = list(REPLACEMENT_MAP.keys())
    print(f"Fetching prices for {len(universe)} names "
           f"({args.start} → {args.end})...")
    prices = fetch_universe_prices(universe, args.start, args.end)
    print(f"Got {len(prices)} rows × {len(prices.columns)} columns")
    print()

    result = simulate(
        prices=prices,
        start_date=args.start,
        end_date=args.end,
        starting_capital=args.capital,
        monthly_contribution=args.monthly,
        min_loss_pct=args.min_loss_pct,
        quality_tilt=args.quality_tilt,
    )

    print(render_summary(result, args.label, args.tax_rate))

    if args.show_swaps and result.get("swaps"):
        print()
        swaps = sorted(result["swaps"], key=lambda s: s["loss"])[:args.show_swaps]
        print(f"  Top {len(swaps)} swaps by loss size:")
        for s in swaps:
            print(f"    {s['date'].date()}  "
                   f"{s['sell']:>5} → {s['buy']:<5}  "
                   f"loss ${s['loss']:>10,.2f}  ({s['loss_pct']*100:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
