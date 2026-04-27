"""Merger arb scanner. Reads deals from data/merger_deals.json + pulls live prices."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.merger_arb import load_deals, scan_deals
from trader.execute import get_last_price


def main():
    deals = load_deals(ROOT / "data" / "merger_deals.json")
    if not deals:
        print("No deals registered in data/merger_deals.json.")
        print("To use: edit that file with real announced M&A deals.")
        print("Sources: InsideArbitrage.com, SEC EDGAR DEFM14A filings, ArbLens.")
        return

    results = scan_deals(deals, price_fetcher=get_last_price)
    print(f"=== MERGER ARB SCAN  ({len(results)} deals) ===\n")
    print(f"{'TICKER':8s}  {'DEAL':>8s}  {'MKT':>8s}  {'SPREAD':>7s}  {'ANN':>7s}  {'DAYS':>5s}  {'BREAK':>6s}  {'EV':>7s}  VERDICT")
    print("-" * 90)
    for r in results:
        d = r.deal
        print(
            f"{d.target_symbol:8s}  ${d.deal_price:>7.2f}  ${r.market_price:>7.2f}  "
            f"{r.spread_pct:>+6.2%}  {r.annualized_yield:>+6.2%}  {r.days_to_close:>4d}d  "
            f"{d.break_risk_estimate:>5.0%}  {r.expected_value:>+6.2%}  {r.verdict}"
        )
    print()
    buys = [r for r in results if r.verdict == "BUY"]
    if buys:
        print(f"✅ {len(buys)} BUY-rated deals. Suggest 1-3% portfolio per deal, max 15% total.")
    else:
        print("No BUY-rated deals today. Either no deals registered or none meet thresholds.")


if __name__ == "__main__":
    main()
