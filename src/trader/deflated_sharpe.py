"""Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).

Corrects an observed Sharpe ratio for:
  1. Selection bias from multiple-trial testing
  2. Non-normality of returns (skew, kurtosis)

We cite this in PAPER.md but never implemented it. Going all the way means
implementing it and folding it into the regression check.

Reference: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
"""
import math
import numpy as np
from scipy import stats


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_observations: int,
    skew: float,
    kurt_excess: float,
    n_trials: int,
) -> tuple[float, float]:
    """Compute the Deflated Sharpe Ratio (DSR) and its associated p-value.

    Args:
        observed_sharpe: the in-sample Sharpe ratio (annualized or not, just be consistent)
        n_observations: number of return observations used to compute Sharpe
        skew: sample skew of returns
        kurt_excess: excess kurtosis (kurtosis - 3) of returns
        n_trials: how many strategy variants were tested before selecting this one

    Returns:
        (deflated_sharpe, p_value) where:
          - deflated_sharpe is the corrected Sharpe (lower than observed)
          - p_value is the probability the deflated Sharpe is purely from luck;
            we want p_value < 0.05 to claim the strategy has real edge.
    """
    if n_observations < 30 or n_trials < 1:
        return float("nan"), float("nan")

    # Expected max Sharpe under null (no skill, n_trials independent strategies)
    # Bailey-Lopez de Prado approximation:
    euler_mascheroni = 0.5772156649
    expected_max_sharpe_null = math.sqrt(2 * math.log(n_trials)) - (
        euler_mascheroni / math.sqrt(2 * math.log(n_trials)) if n_trials > 1 else 0
    )

    # Variance of the Sharpe estimator (corrects for non-normality)
    sharpe_var = (
        1 - skew * observed_sharpe + ((kurt_excess - 1) / 4) * observed_sharpe ** 2
    ) / (n_observations - 1)

    if sharpe_var <= 0:
        return float("nan"), float("nan")

    sharpe_se = math.sqrt(sharpe_var)
    z = (observed_sharpe - expected_max_sharpe_null) / sharpe_se
    # DSR proper (Bailey-Lopez de Prado): probability the TRUE Sharpe exceeds
    # what selection bias from n_trials would produce by chance. Want > 0.95.
    dsr_probability = float(stats.norm.cdf(z))
    p_value = float(1 - dsr_probability)  # one-sided test that strategy is real
    return dsr_probability, p_value


def pretty_print(observed: float, dsr: float, p_value: float, n_trials: int):
    print(f"  observed Sharpe:    {observed:+.2f}")
    print(f"  DSR probability:    {dsr:.1%}  (probability strategy has real edge given {n_trials} trials)")
    print(f"  p-value:            {p_value:.4f}")
    if dsr > 0.95:
        print("  VERDICT: edge is statistically significant after correction (95%+ confidence)")
    elif dsr > 0.80:
        print("  VERDICT: edge is plausible (80-95% confidence)")
    elif dsr > 0.50:
        print("  VERDICT: edge is borderline; insufficient evidence either way")
    else:
        print("  VERDICT: edge likely from selection bias — cannot distinguish from random")
