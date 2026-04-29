# trader — context

## What this is

Personal automated trading system. Alpaca paper account. Daily-run cron + GitHub Actions.

## Meta-process (every iteration)

**Hypothesis → Test → Iterate → System-level fold-in.** Don't ship code without
backtest evidence. Don't promote a variant without 5-regime stress-test wins.
Don't accept a "win in one regime" — survivorship-bias yourself by demanding
robustness across 2018-Q4 / 2020-Q1 / 2022 / 2023 / recent.

**Step back every 3-4 versions** and audit the system holistically. Don't just
ship more variants — periodically ask: what's been tested, what hasn't, what
hidden methodology bugs might be inflating our backtest numbers, what gaps
exist between paper-trading and live-readiness? List 5-10 candidate
modifications, prioritize by expected_value × probability × cost. Be willing
to invalidate prior conclusions with better methodology (e.g., point-in-time
universe could revise the +1.48 Sharpe down to OOS-honest +0.7).

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
- Bond market + VIX term structure overlays (v3.7 — 7 variants tested both as
  defensive cuts AND contrarian adds). HYG/LQD credit spread, T10Y2Y curve,
  VIX9D/VIX/VIX3M backwardation, SKEW. ALL FAIL: 0.80-0.90 mean Sharpe vs
  LIVE 1.54. Stress signals are LATE — fire at panic lows; cutting at lows =
  selling bottoms; adding at lows = -38% worst DD when stress is mid-trend.
  Lesson: macro signals are real leading indicators for risk management /
  alerting, but useless as portfolio overlays on top of momentum. Modules
  kept as libraries (src/trader/macro.py, src/trader/vol_signals.py).
- Kalshi / Polymarket prediction-market data. NOT tested but documented as
  low-EV: derivative of macro narrative (already failed), thin liquidity
  (<$100k typical), few markets persist >1yr for backtest.
- Multi-asset trend-following (v3.19, Hurst-Ooi-Pedersen 2024 framework).
  9-ETF universe (SPY/QQQ/EFA/EEM/GLD/TLT/IEF/DBC/VNQ), 12-1 absolute
  momentum. Mean Sharpe +0.05 vs LIVE +1.54. Worst-DD better (-17 vs -25%)
  but mean CAGR collapsed to +1.1%. Crisis alpha thesis didn't materialize
  in our 5 regime windows. The asset-class diversification is real protection
  but the opportunity cost in trending equity bulls is too large.
- Quality screen on momentum (v3.20, Asness QMJ + Greenblatt). Top-3 by
  composite quality score (ROE / margin / D/E) among top-10 momentum names.
  Mean Sharpe +0.81 vs LIVE +1.54. Worst-DD WORSE (-32% vs -25%). Forward-
  look bias in current quality metrics; structural quality may filter wrong
  way for momentum (filters OUT cyclicals that have momentum in those windows).

## v3.25 META-FINDING: ALL shadow variant edges are survivor-bias artifacts

After PIT-validating every shadow that previously claimed edge over LIVE:

| Variant | Survivor Sharpe | PIT Sharpe | PIT vs PIT-baseline (+0.98) |
|---|---|---|---|
| top3_residual (v3.15) | +1.53 | **+0.03** | **-0.95** |
| top3_residual_voltgt (v3.16) | **+1.61** "best ever" | **-0.24** | **-1.22** |
| top3_crowding (v3.21) | +1.72 | +0.60 | -0.38 |

**ALL THREE FAIL.** Edges that looked like +0.07 to +0.18 over LIVE on the
survivor universe collapse to -0.38 to -1.22 on the honest PIT universe.

The "+1.61 best ever measured" claim for v3.16 was particularly misleading —
on PIT it's actually NEGATIVE Sharpe. The signal makes the strategy worse
on the broader universe.

## Strategic implications (post v3.25)

1. **No shadow variant has measurable edge on the honest universe.** All
   research-paper signals tested (residual momentum, vol-targeting,
   crowding penalty, multi-asset trend, quality, trend-R²) FAIL PIT
   validation.

2. **LIVE strategy unchanged**: top3_eq_80 12-1 momentum is the best of
   what we've tested. Honest expectation: +0.98 Sharpe, +19% CAGR, -33%
   worst-DD on PIT-corrected backtest.

3. **Future iterations should NOT focus on signal stacking.** Expected
   value is near zero based on 7+ failed attempts. Better targets:
   - PIT-aware execution (limit orders, TWAP)
   - Cost reduction (rebalance frequency tuning)
   - Position-cap testing at small accounts ($10k Roth IRA)
   - Tax-aware sequencing
   - Behavioral risk infrastructure (drawdown alerts, max-loss kill)

4. **Mandatory PIT-required gate**: any future variant must pass:
   - Survivor backtest: ≥3/5 regime wins, no worse worst-MaxDD vs LIVE
   - **PIT validation**: must beat PIT baseline +0.98 by ≥0.10 mean Sharpe
   No exceptions. Claimed survivor-edges without PIT validation are noise.

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
