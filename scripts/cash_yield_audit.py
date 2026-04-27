"""Cash-yield audit. Shows what idle cash COULD be earning vs what it IS earning."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.execute import get_client


ALPACA_CASH_APY = 0.04         # ~4% as of 2026 (verify current rate)
T_BILL_4WK_APY = 0.045          # ~4.5% recent 4-week T-Bill
BOX_SPREAD_APY = 0.048           # ~4.8% recent SPX 1y box (needs IBKR or TastyTrade)
HIGH_YIELD_SAVINGS = 0.045      # Wealthfront, Marcus, etc.


def main():
    c = get_client()
    acct = c.get_account()
    cash = float(acct.cash)
    equity = float(acct.equity)
    cash_pct = cash / equity

    print("=== CASH YIELD AUDIT ===\n")
    print(f"Total equity:    ${equity:>12,.2f}")
    print(f"Idle cash:       ${cash:>12,.2f}  ({cash_pct:.0%} of portfolio)")
    print(f"Active deployed: ${equity - cash:>12,.2f}")
    print()

    print(f"{'Vehicle':30s}  {'APY':>6s}  {'Annual on idle cash':>22s}  {'Notes':40s}")
    print("-" * 110)
    options = [
        ("Alpaca idle cash (current)", ALPACA_CASH_APY, "already on, no action needed"),
        ("4-week T-Bills via TreasuryDirect", T_BILL_4WK_APY, "true riskless; outside Alpaca; 50bp upgrade"),
        ("SPX box spreads (1-year)", BOX_SPREAD_APY, "NEEDS IBKR or TastyTrade; not Alpaca"),
        ("High-yield savings (Wealthfront/Marcus)", HIGH_YIELD_SAVINGS, "liquid, FDIC, easy transfer"),
    ]
    for name, apy, note in options:
        annual = cash * apy
        print(f"{name:30s}  {apy:>5.2%}  ${annual:>20,.2f}  {note}")

    print()
    upgrade_yield = (T_BILL_4WK_APY - ALPACA_CASH_APY) * cash
    print(f"Upgrade from Alpaca cash to T-Bills: +${upgrade_yield:,.2f}/yr riskless.")
    print("Action: open TreasuryDirect.gov account (free, 5 min), buy 4-week T-Bills,")
    print("        roll weekly. Ladder is true riskless; Alpaca cash has Apex bank risk.")


if __name__ == "__main__":
    main()
