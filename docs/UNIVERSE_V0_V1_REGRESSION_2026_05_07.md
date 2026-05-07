# Universe V0 vs V1 — Regression Test

**Date:** 2026-05-07  
**Goal:** test whether the 77-name expansion proposed in UNIVERSE_V1_2026_05_07.md preserves the LIVE strategy's alpha and risk profile.

## Test setup

- Same LIVE strategy (xs_top15 12-1 momentum, min-shift weighting, 80% gross)
- 25-year panel (2000-01-01 → today)
- Monthly rebalance, multiplicative compounding

## Results

| Metric | V0 (existing) | V1 (broader) | Delta |
|---|---:|---:|---:|
| Universe size | 43 | 120 | +77 |
| Cum return (×) | 79.6183 | 59.5977 | -20.0206 |
| Max drawdown | -35.74% | -36.65% | -0.91pp |
| IR (annualized) | 0.970 | 0.931 | -0.039 |

## Most recent picks comparison

As of 2026-04-30:

- V0 chose: `['AMD', 'BAC', 'CAT', 'CSCO', 'GS', 'INTC', 'JNJ', 'JPM', 'MRK', 'MS', 'NVDA', 'PFE', 'VZ', 'WMT', 'XOM']`
- V1 chose: `['ADM', 'AMAT', 'AMD', 'C', 'CAT', 'FCX', 'FDX', 'GS', 'INTC', 'JNJ', 'KLAC', 'LRCX', 'MU', 'NEM', 'NVDA']`
- New in V1: `['ADM', 'AMAT', 'C', 'FCX', 'FDX', 'KLAC', 'LRCX', 'MU', 'NEM']`
- Dropped from V0: `['BAC', 'CSCO', 'JPM', 'MRK', 'MS', 'PFE', 'VZ', 'WMT', 'XOM']`

## Decision rule

Ship V1 if:
- IR doesn't drop more than 0.10
- Max-DD doesn't worsen by more than 5pp

- IR drop: +0.039 → ✅ pass
- DD worsening: +0.91pp → ✅ pass

## Verdict: ✅ SHIP

V1 preserves the LIVE strategy's profile within the decision threshold. Recommend swapping `sectors.SECTORS` to the merged V1 universe in a follow-up commit.
