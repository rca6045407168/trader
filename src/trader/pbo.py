"""Probability of Backtest Overfitting (PBO) — Bailey, Borwein, Lopez de Prado, Zhu (2014).

Alongside DSR, PBO measures the probability that the strategy SELECTED IN-SAMPLE
will UNDERPERFORM THE MEDIAN of all tested strategies out-of-sample.

Intuition: if you tested 12 strategies, picked the best one in-sample, and that
best-in-sample is worse than median in OOS, the selection process is overfitting.

Methodology (Combinatorial Symmetric Cross-Validation, CSCV):
  1. Partition return matrix M (T x N strategies) into S equal-size groups by time
  2. For each combination of S/2 groups (training), use complement (testing)
  3. In each split: rank strategies by Sharpe in TRAIN, observe rank of best-train in TEST
  4. PBO = P(best in-sample strategy ranks below median out-of-sample)

We want PBO < 0.50 (less than 50% chance of being below median = better than coin flip).
Academic threshold: PBO < 0.20.
"""
import itertools
import math
import numpy as np
import pandas as pd


def pbo_from_returns(returns_matrix: pd.DataFrame, n_partitions: int = 16) -> dict:
    """Compute PBO via CSCV given a (T x N) return matrix.

    Args:
        returns_matrix: rows are time periods, columns are strategy variants.
            Each cell is the return of strategy column in time period row.
        n_partitions: number of equal-size time partitions (must be even).

    Returns:
        {
          "pbo": probability of backtest overfitting (0 to 1),
          "n_combinations": how many train/test splits were evaluated,
          "n_strategies": number of strategies considered,
          "verdict": "OK" / "caution" / "overfit",
        }
    """
    T, N = returns_matrix.shape
    if N < 2:
        return {"pbo": float("nan"), "verdict": "insufficient_strategies"}
    if T < 2 * n_partitions:
        return {"pbo": float("nan"), "verdict": "insufficient_observations"}
    if n_partitions % 2 != 0:
        n_partitions += 1

    # Split rows into n_partitions equal chunks
    chunk_size = T // n_partitions
    chunks = [returns_matrix.iloc[i * chunk_size: (i + 1) * chunk_size] for i in range(n_partitions)]

    # All combinations of n_partitions/2 chunks for training
    train_combos = list(itertools.combinations(range(n_partitions), n_partitions // 2))

    rank_logits = []
    for train_idx in train_combos:
        test_idx = [i for i in range(n_partitions) if i not in train_idx]
        train_data = pd.concat([chunks[i] for i in train_idx])
        test_data = pd.concat([chunks[i] for i in test_idx])

        # Sharpe per strategy (annualized assumption irrelevant; just rank)
        train_sharpe = train_data.mean() / train_data.std().replace(0, np.nan)
        test_sharpe = test_data.mean() / test_data.std().replace(0, np.nan)

        if train_sharpe.isna().all() or test_sharpe.isna().all():
            continue

        # Best in-sample
        best_in_strat = train_sharpe.idxmax()
        # Its rank in OOS
        oos_rank = test_sharpe.rank(ascending=True).get(best_in_strat, np.nan)
        if pd.isna(oos_rank):
            continue
        # Logit transform: how far below median?
        oos_relative_rank = (oos_rank - 1) / (N - 1)  # 0 = worst, 1 = best
        if 0 < oos_relative_rank < 1:
            logit = math.log(oos_relative_rank / (1 - oos_relative_rank))
            rank_logits.append(logit)

    if not rank_logits:
        return {"pbo": float("nan"), "verdict": "no_valid_combinations"}

    pbo = sum(1 for x in rank_logits if x < 0) / len(rank_logits)

    if pbo < 0.20:
        verdict = "OK"
    elif pbo < 0.50:
        verdict = "caution"
    else:
        verdict = "overfit"

    return {
        "pbo": float(pbo),
        "n_combinations": len(rank_logits),
        "n_strategies": N,
        "n_partitions": n_partitions,
        "verdict": verdict,
    }
