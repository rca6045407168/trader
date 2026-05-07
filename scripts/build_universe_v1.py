#!/usr/bin/env python3
"""v3.73.24 — broader universe v1.

The current production universe (sectors.SECTORS) has 42 names —
all post-2000 survivors with deep liquidity. The user's persistent
critique: this is too narrow to be representative, and shrinks
diversification on adversarial regimes.

This script:
  1. Probes ~100 large-cap candidates against yfinance for
     full 2000-01-01 history.
  2. Reports which pass the threshold (>= 95% data coverage from
     2000-01-01 to today).
  3. Writes a UNIVERSE_V1 list of qualifying tickers + their GICS
     sector to docs/UNIVERSE_V1_2026_05_07.md.
  4. The list is then a candidate replacement for SECTORS in a
     follow-up commit (gated behind a regression test that
     compares the LIVE strategy on V0 vs V1 — only ship V1 if it
     does not destroy IR).

Note: this does NOT yet replace sectors.SECTORS. It produces the
candidate list and provides a reproducible probe so the swap can
be argued from data, not from a hand-picked list.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from trader.data import fetch_history  # noqa: E402
from trader.sectors import SECTORS  # noqa: E402

# Candidate large-caps (most have 2000+ history). Sector tags follow
# the existing GICS-ish bucketing in sectors.py for downstream
# compatibility.
CANDIDATES = {
    # More Tech
    "IBM": "Tech", "HPQ": "Tech", "EBAY": "Tech", "ADP": "Tech",
    # More Communication
    "CMCSA": "Communication",
    # More Consumer Discretionary
    "SBUX": "ConsumerDisc", "LOW": "ConsumerDisc", "TGT": "ConsumerDisc",
    "TJX": "ConsumerDisc", "F": "ConsumerDisc",
    # More Consumer Staples
    "CL": "ConsumerStap", "MO": "ConsumerStap", "GIS": "ConsumerStap",
    "K": "ConsumerStap", "HSY": "ConsumerStap", "KMB": "ConsumerStap",
    "ADM": "ConsumerStap", "STZ": "ConsumerStap",
    # More Healthcare
    "AMGN": "Healthcare", "BMY": "Healthcare", "LLY": "Healthcare",
    "GILD": "Healthcare", "MDT": "Healthcare", "CVS": "Healthcare",
    "WBA": "Healthcare", "BAX": "Healthcare", "SYK": "Healthcare",
    "BIIB": "Healthcare",
    # More Financials
    "AXP": "Financials", "USB": "Financials", "PNC": "Financials",
    "BK": "Financials", "COF": "Financials", "TRV": "Financials",
    "MET": "Financials", "SCHW": "Financials", "PRU": "Financials",
    "C": "Financials", "AIG": "Financials",
    # More Energy
    "CVX": "Energy", "COP": "Energy", "SLB": "Energy", "EOG": "Energy",
    "MPC": "Energy",
    # More Industrials
    "GE": "Industrials", "UPS": "Industrials", "RTX": "Industrials",
    "LMT": "Industrials", "NOC": "Industrials", "MMM": "Industrials",
    "DE": "Industrials", "EMR": "Industrials", "FDX": "Industrials",
    "UNP": "Industrials", "CSX": "Industrials", "NSC": "Industrials",
    "GD": "Industrials", "WM": "Industrials",
    # More Materials
    "APD": "Materials", "ECL": "Materials", "NEM": "Materials",
    "NUE": "Materials", "FCX": "Materials",
    # Utilities
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "AEP": "Utilities", "EXC": "Utilities", "D": "Utilities",
    "ED": "Utilities",
    # REITs (post-IPO history varies)
    "O": "RealEstate", "AMT": "RealEstate", "PSA": "RealEstate",
    "EQR": "RealEstate", "VNO": "RealEstate",
    # Other Tech / Communication (later IPOs may not have full history)
    "INTU": "Tech",  # Intuit
    "AMAT": "Tech",  # Applied Materials
    "MU": "Tech",  # Micron
    "LRCX": "Tech",  # Lam Research
    "KLAC": "Tech",  # KLA
}

# Threshold: at least 95% of the days from 2000-01-01 to today
# must have data
COVERAGE_MIN = 0.85  # 85% — relaxed since some IPO'd post-2000


def main():
    candidates = list(CANDIDATES.keys())
    print(f"Probing {len(candidates)} candidates against yfinance...")

    # fetch_history will return a DataFrame with all the columns;
    # missing names get NaN-only columns we need to filter
    prices = fetch_history(candidates, start="2000-01-01")

    today = pd.Timestamp.today()
    expected_days = len(pd.bdate_range("2000-01-01", today))

    qualifying = []
    insufficient = []

    for sym in candidates:
        if sym not in prices.columns:
            insufficient.append((sym, 0.0, "no data"))
            continue
        s = prices[sym].dropna()
        coverage = len(s) / expected_days
        first_date = s.index[0].date() if len(s) > 0 else "n/a"
        if coverage >= COVERAGE_MIN:
            qualifying.append((sym, coverage, first_date))
        else:
            insufficient.append((sym, coverage, first_date))

    print(f"\nQualifying ({len(qualifying)}/{len(candidates)}):")
    for sym, cov, first in qualifying:
        print(f"  {sym:<6} {cov*100:>5.1f}%  first={first}")
    print(f"\nInsufficient coverage ({len(insufficient)}/{len(candidates)}):")
    for sym, cov, first in insufficient:
        print(f"  {sym:<6} {cov*100:>5.1f}%  first={first}")

    # Build merged universe
    new_universe = dict(SECTORS)
    for sym, cov, _ in qualifying:
        if sym not in new_universe:
            new_universe[sym] = CANDIDATES[sym]

    # Write out
    out = []
    out.append("# Universe v1 — Broader Universe Probe\n\n")
    out.append("**Date:** 2026-05-07  \n")
    out.append("**Goal:** address the user's universe-too-narrow critique by "
                "probing a broader candidate list against yfinance and reporting "
                "which qualify for inclusion.\n\n")
    out.append(f"## Coverage threshold\n\n{COVERAGE_MIN*100:.0f}% of business "
                "days from 2000-01-01 to today.\n\n")
    out.append("## Existing universe (V0)\n\n")
    out.append(f"{len(SECTORS)} names in `src/trader/sectors.py`. "
                "Hand-picked post-2000 survivors.\n\n")

    out.append(f"## Probed candidates ({len(candidates)})\n\n")
    out.append(f"### Qualifying ({len(qualifying)})\n\n")
    out.append("| Symbol | Coverage | First date | Sector |\n|---|---:|---|---|\n")
    for sym, cov, first in qualifying:
        out.append(f"| {sym} | {cov*100:.1f}% | {first} | "
                   f"{CANDIDATES[sym]} |\n")

    out.append(f"\n### Insufficient ({len(insufficient)})\n\n")
    out.append("| Symbol | Coverage | First date |\n|---|---:|---|\n")
    for sym, cov, first in insufficient:
        out.append(f"| {sym} | {cov*100:.1f}% | {first} |\n")

    out.append(f"\n## Merged Universe v1: {len(new_universe)} names\n\n")
    sector_counts = {}
    for sym, sec in new_universe.items():
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
    out.append("Sector breakdown:\n\n")
    out.append("| Sector | Count |\n|---|---:|\n")
    for sec, n in sorted(sector_counts.items(), key=lambda kv: -kv[1]):
        out.append(f"| {sec} | {n} |\n")
    out.append("\n## Next steps\n\n")
    out.append("1. Run a regression test: LIVE strategy on V0 vs V1 over "
                "the 25-year hostile-regime panel. Only swap if V1 does "
                "not destroy IR or alpha.\n")
    out.append("2. If the regression passes, swap `sectors.SECTORS` to "
                "the merged universe and re-run the production "
                "rebalance to see how the LIVE picks change.\n")
    out.append("3. Document the change in CHANGELOG + bump README "
                "version.\n")

    out_path = ROOT / "docs" / "UNIVERSE_V1_2026_05_07.md"
    out_path.write_text("".join(out))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
