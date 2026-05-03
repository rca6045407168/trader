"""[v3.60.0] SHADOW scorer wiring — alternative scoring methods that
compute alongside LIVE momentum to enable continuous A/B comparison.

Per BLINDSPOTS §11 + the v5 ladder: build the empirical case for every
candidate scoring method by running it as SHADOW alongside LIVE.
After 30+ days of overlap, compare:
  • Pick set overlap with LIVE
  • Implied portfolio return delta
  • Sharpe / drawdown if it had been LIVE

Then promote or kill based on data, not vibes.

This module exposes:
  • compute_shadow_picks(asof) → {scorer_name: [tickers]}
  • write_shadow_picks(asof, picks_dict) → persists to data/shadow_picks.csv
  • compare_shadow_vs_live(window_days) → comparison stats for each shadow

Scorers wired:
  • vanilla_momentum: 12-1 trailing return (current LIVE)
  • residual_momentum: Blitz-Hanauer factor-neutral version
  • cost_aware_momentum: vanilla + ADV/spread filter
"""
from __future__ import annotations

import csv
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


SHADOW_PICKS_CSV = DATA_DIR / "shadow_picks.csv"
SHADOW_HEADERS = ["date", "scorer", "ranked_picks"]


def vanilla_momentum_picks(universe: list[str],
                              asof: Optional[str] = None,
                              top_n: int = 15) -> list[str]:
    """The current LIVE scorer. Wraps rank_momentum."""
    from .strategy import rank_momentum
    cands = rank_momentum(universe, lookback_months=12, skip_months=1,
                            top_n=top_n, end_date=asof)
    return [c.ticker for c in cands]


def residual_momentum_picks(universe: list[str],
                              asof: Optional[str] = None,
                              top_n: int = 15,
                              lookback_months: int = 12) -> list[str]:
    """Blitz-Hanauer residual momentum. Strips Fama-French 5 factor
    loadings before computing momentum — isolates the idiosyncratic
    component that persists.

    Falls back to empty list on data failure (network, FF5 fetch).
    """
    try:
        import pandas as pd
        from .data import fetch_history
        from .residual_momentum import top_n_residual_momentum, get_ff5_aligned
        end = pd.Timestamp(asof) if asof else pd.Timestamp.today()
        # Need 36 months for FF5 regression + 12 for momentum
        start = (end - pd.DateOffset(months=lookback_months + 36 + 2)).strftime("%Y-%m-%d")
        prices = fetch_history(universe, start=start)
        if prices.empty:
            return []
        # Clip to as-of
        if asof is not None:
            prices = prices[prices.index <= end]
        ff5 = get_ff5_aligned()
        if asof is not None:
            ff5 = ff5[ff5.index <= end]
        picks = top_n_residual_momentum(prices, ff5,
                                          lookback_months=lookback_months,
                                          top_n=top_n)
        # picks is a list of (ticker, score) tuples
        return [t[0] if isinstance(t, tuple) else t for t in picks]
    except Exception:
        return []


def cost_aware_momentum_picks(universe: list[str],
                                asof: Optional[str] = None,
                                top_n: int = 15,
                                min_adv_dollar: float = 50_000_000) -> list[str]:
    """Vanilla momentum + ADV filter. Drops names whose 30-day average
    dollar volume is below min_adv_dollar (default $50M, plenty for a
    $10K account but a real screen for capacity scaling).

    On data failure, returns vanilla momentum (the screen is best-effort).
    """
    base = vanilla_momentum_picks(universe, asof, top_n=top_n * 2)
    if not base:
        return base
    try:
        import pandas as pd
        from .data import fetch_ohlcv
        end = pd.Timestamp(asof) if asof else pd.Timestamp.today()
        start = (end - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        kept = []
        for sym in base:
            try:
                ohlc = fetch_ohlcv(sym, start=start)
                if asof:
                    ohlc = ohlc[ohlc.index <= end]
                if ohlc.empty:
                    continue
                # Use last 30 trading days
                tail = ohlc.tail(30)
                if "Volume" in tail.columns and "Close" in tail.columns:
                    avg_dollar = (tail["Volume"] * tail["Close"]).mean()
                    if isinstance(avg_dollar, pd.Series):
                        avg_dollar = float(avg_dollar.iloc[0])
                    else:
                        avg_dollar = float(avg_dollar)
                    if avg_dollar >= min_adv_dollar:
                        kept.append(sym)
                else:
                    # Couldn't compute ADV — keep by default
                    kept.append(sym)
            except Exception:
                # Fail-open: keep the candidate
                kept.append(sym)
            if len(kept) >= top_n:
                break
        return kept[:top_n] if kept else base[:top_n]
    except Exception:
        return base[:top_n]


def compute_shadow_picks(universe: list[str],
                           asof: Optional[str] = None,
                           top_n: int = 15) -> dict[str, list[str]]:
    """Run all 3 scorers, return {scorer_name: [picks]}."""
    return {
        "vanilla_momentum": vanilla_momentum_picks(universe, asof, top_n),
        "residual_momentum": residual_momentum_picks(universe, asof, top_n),
        "cost_aware_momentum": cost_aware_momentum_picks(universe, asof, top_n),
    }


def write_shadow_picks(asof: str, picks: dict[str, list[str]]) -> bool:
    """Append picks to data/shadow_picks.csv. Idempotent on (date, scorer)."""
    SHADOW_PICKS_CSV.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if SHADOW_PICKS_CSV.exists():
        try:
            with SHADOW_PICKS_CSV.open() as f:
                existing = list(csv.DictReader(f))
        except Exception:
            existing = []
    # Drop any (asof, scorer) duplicates
    keep = [r for r in existing
            if not (r.get("date") == asof and r.get("scorer") in picks)]
    for scorer, picks_list in picks.items():
        keep.append({"date": asof, "scorer": scorer,
                       "ranked_picks": ",".join(picks_list)})
    keep.sort(key=lambda r: (r["date"], r["scorer"]))
    try:
        with SHADOW_PICKS_CSV.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SHADOW_HEADERS)
            w.writeheader()
            w.writerows(keep)
        return True
    except Exception:
        return False


def read_shadow_history() -> list[dict]:
    """Returns all rows from shadow_picks.csv."""
    if not SHADOW_PICKS_CSV.exists():
        return []
    try:
        with SHADOW_PICKS_CSV.open() as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def overlap_metrics() -> dict:
    """For each (vanilla → residual / cost_aware) pair, compute the
    average pick-set overlap across all dates with both scorers
    recorded. Useful diagnostic: low overlap = scorers disagree;
    high overlap = scorers redundant."""
    rows = read_shadow_history()
    if not rows:
        return {"n_dates": 0}
    by_date: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        d = r["date"]
        s = r["scorer"]
        picks = r["ranked_picks"].split(",") if r["ranked_picks"] else []
        by_date.setdefault(d, {})[s] = picks

    results = {}
    pairs = [("vanilla_momentum", "residual_momentum"),
              ("vanilla_momentum", "cost_aware_momentum"),
              ("residual_momentum", "cost_aware_momentum")]
    for a, b in pairs:
        overlaps = []
        for d, scorers in by_date.items():
            if a in scorers and b in scorers:
                set_a = set(scorers[a])
                set_b = set(scorers[b])
                if set_a:
                    overlaps.append(len(set_a & set_b) / len(set_a))
        if overlaps:
            results[f"{a}_vs_{b}"] = {
                "n_dates": len(overlaps),
                "mean_overlap": sum(overlaps) / len(overlaps),
                "min_overlap": min(overlaps),
                "max_overlap": max(overlaps),
            }
    results["n_dates"] = len(by_date)
    return results
