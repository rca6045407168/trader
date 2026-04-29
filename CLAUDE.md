# trader — context

## What this is

Personal automated trading system. Alpaca paper account. Daily-run cron + GitHub Actions.

## Meta-process (every iteration)

**Hypothesis → Test → Iterate → System-level fold-in.** Don't ship code without
backtest evidence. Don't promote a variant without 5-regime stress-test wins.
Don't accept a "win in one regime" — survivorship-bias yourself by demanding
robustness across 2018-Q4 / 2020-Q1 / 2022 / 2023 / recent.

When introducing new components (signals, sleeves, allocators):
1. Form the hypothesis explicitly (1-line: what edge, why does it persist)
2. Backtest in `scripts/regime_stress_test.py` across all 5 regimes
3. If mean Sharpe + worst MaxDD beat the deployed strategy: register as SHADOW
4. After ~30 live trading days of shadow data with paired_test() significant:
   promote to LIVE
5. Bake the result back into the docstring of the script — the negative
   results (what we tried + killed) matter as much as the positive ones

**Killed candidates so far** (don't re-propose without new evidence):
- Risk-parity sleeve weighting (cash drag, no Sharpe edge)
- Dual-momentum GEM (bad regime timer, -0.36 mean Sharpe)
- Anomaly overlay on portfolio (compounds whipsaw, killed 2022 -14pp)
- top-1 / top-2 concentration (idiosyncratic noise > diversification benefit)
- top-5/top-10 dilution (water down momentum picks)
- Regime-aware meta-allocator with asset-class swap (200d MA + VIX → cash/SPY).
  Failed gate: only 1.5/5 regimes won, mean Sharpe 1.04 vs LIVE 1.48. Defensive
  cuts get caught at V-shape lows (2020-Q1 cost -34pp). Lesson: momentum
  strategies have built-in regime adaptation via monthly rebalance; adding an
  explicit regime layer creates whipsaw + double-counting. If trying again,
  use position-sizing tweaks (cut allocation 80→50%) NOT asset-class swaps.

## What's deployed

LIVE: `momentum_top3_aggressive_v1` — top-3 12mo cross-sectional momentum, 26.7%/name (80% gross).
Risk gates in `risk_manager.py` (per-name 30%, gross 95%, daily-loss halt -3%, DD halt -8%, VIX scaling).

Email goes to **richard.chen.1989@gmail.com** (personal). Not the FlexHaul work
address. Stub guard in `notify.py` blocks `<80 char` bodies. Don't trigger emails
for normal iterations — only for material findings or daily report.

## Scheduled routines (signals to incorporate where appropriate)

- `trader-anomaly-scan` — calendar anomalies (advisory only; killed as overlay)
- `trader-daily-run` — strategy execution
- `trader-monthly-walkforward` — param sweep (advisory)
- `trader-monthly-dsr-audit` — selection-bias correction
- `trader-weekly-degradation-check` — drift monitoring
- `trader-research-paper-scanner` — new arxiv/SSRN ideas
