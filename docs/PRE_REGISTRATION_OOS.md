# Pre-Registered OOS Evaluation Window

**Created: 2026-04-29 (locked, no editing after this point)**

This document declares OUT-OF-SAMPLE evaluation windows BEFORE we collect
the data. No peeking. No parameter adjustments mid-window. No cherry-picking
the start date. This is the scientific protocol that distinguishes real
backtests from data-mined ones.

## Why pre-registration matters

After v3.36's CPCV check (which invalidated the HMM aggressive +0.24 Sharpe
"win"), it's clear that 5-regime stress tests give point estimates with
huge error bars. The way to honestly evaluate ANY strategy is:

1. Lock the strategy parameters (no tuning after start of evaluation)
2. Lock the universe (no swapping mid-test)
3. Lock the evaluation criteria (no moving the goalposts)
4. Lock the start/end dates (no extending or truncating)
5. ONLY THEN measure

## The evaluation window

**Strategies under evaluation:**
- `momentum_top3_aggressive_v1` (LIVE, control)
- `momentum_top3_hmm_aggressive_v1` (HMM-aggressive, candidate per v3.32)
- `momentum_top15_mom_weighted_v1` (top-15 mom-weighted, per v3.29)

**Window:** 2026-05-01 → 2026-10-31 (6 months, ~125 trading days)

**Universe:** DEFAULT_LIQUID_50 (frozen as of 2026-04-29)

**Promotion criteria** (variant must satisfy ALL):
1. Realized Sharpe over the window > LIVE realized Sharpe + 0.15
2. Bootstrap-CI 5th percentile of edge > 0
3. Worst drawdown ≤ LIVE worst drawdown + 5pp
4. paired_test() p-value < 0.05 vs LIVE returns

**Failure criteria** (variant retired):
- Realized Sharpe < LIVE Sharpe - 0.30 → retire
- Worst-DD > LIVE worst-DD + 10pp → retire (bigger drawdown than LIVE
  with materially worse Sharpe)

**Tied criteria** (extend evaluation window):
- Sharpe within ±0.15 of LIVE → continue tracking another 6 months

## Methodology safeguards

1. **No parameter changes mid-window.** If we discover a variant has a bug
   that needs fixing, we RESTART the evaluation with a new pre-registration.
2. **All variants run via the existing daily-run + shadow logging pipeline.**
   No special infrastructure for the candidate.
3. **Realized returns measured from `shadow_decisions` table replay,**
   not from any backtest framework. This catches implementation bugs that
   don't show in the regime stress test.

## Non-goals

This pre-registration is NOT:
- A commitment to deploy the winner. After window close we can still
  decline to promote (e.g., if the winner has unacceptable tax efficiency
  or operational complexity).
- A statement that LIVE is broken. LIVE remains LIVE until explicitly
  replaced.

## Sign-off

Locked at git commit hash: (to be filled by next commit)
SHA-256 of this file at lock time: (to be computed)

Author: Richard Chen + WOZCODE collaboration
Date locked: 2026-04-29
Window starts: 2026-05-01
Window ends: 2026-10-31

## Re-evaluation

Re-read this file on or after 2026-10-31. ONLY THEN measure realized stats
against the criteria above. Update CLAUDE.md with the result.
