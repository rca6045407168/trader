"""Apply Deflated Sharpe to the deployed strategy. Honest measurement after
the ~12 hypotheses we tested across iterate_v3..v11."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
from trader.backtest import backtest_momentum_realistic
from trader.universe import DEFAULT_LIQUID_50
from trader.deflated_sharpe import deflated_sharpe_ratio, pretty_print

N_TRIALS_TESTED = 12  # walk-forward + bottom-catch params + risk-parity variants + breakout, etc.


def main():
    print("=" * 78)
    print("DEFLATED SHARPE AUDIT  —  honest correction for selection bias")
    print("=" * 78)
    print(f"\nN_trials assumed: {N_TRIALS_TESTED}")

    # Realistic backtest (B4 fix) is our most honest result
    r = backtest_momentum_realistic(
        DEFAULT_LIQUID_50, start="2015-01-01", end="2025-04-30",
        lookback_months=12, top_n=5,
    )
    monthly = r.monthly_returns.dropna()
    n_obs = len(monthly)
    obs_sharpe = monthly.mean() * 12 / (monthly.std() * (12 ** 0.5))
    skew = monthly.skew()
    kurt = monthly.kurtosis()
    print(f"\nN observations: {n_obs} months")
    print(f"Skew: {skew:+.2f}  Excess kurtosis: {kurt:+.2f}")
    print("\n--- under {} effectively-independent trials ---".format(N_TRIALS_TESTED))
    dsr, p_val = deflated_sharpe_ratio(
        observed_sharpe=obs_sharpe, n_observations=n_obs,
        skew=skew, kurt_excess=kurt, n_trials=N_TRIALS_TESTED,
    )
    pretty_print(obs_sharpe, dsr, p_val, N_TRIALS_TESTED)

    # Also try fewer trials — our 12 hypotheses were heavily correlated iterations,
    # so the EFFECTIVE independent-trials count is more like 3-5
    print("\n--- under 5 effectively-independent trials (correcting for correlation) ---")
    dsr5, p5 = deflated_sharpe_ratio(
        observed_sharpe=obs_sharpe, n_observations=n_obs,
        skew=skew, kurt_excess=kurt, n_trials=5,
    )
    pretty_print(obs_sharpe, dsr5, p5, 5)

    print("\n--- under 3 effectively-independent trials (most generous) ---")
    dsr3, p3 = deflated_sharpe_ratio(
        observed_sharpe=obs_sharpe, n_observations=n_obs,
        skew=skew, kurt_excess=kurt, n_trials=3,
    )
    pretty_print(obs_sharpe, dsr3, p3, 3)

    print("\nCAVEAT: Deflated Sharpe is a heuristic correction. The most")
    print("reliable test remains the walk-forward result on held-out data.")
    print("OOS Sharpe (2021-2025) was 0.76 — consistent with deflation suggesting")
    print("the in-sample number is selection-biased.")


if __name__ == "__main__":
    main()
