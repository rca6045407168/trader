# Go-Live Checklist

**Source of truth for the paper-to-live transition.** Built v3.17 in response to
"we are currently testing for one month before going live, what concerns?"

The honest answer: **a one-month paper test is statistically meaningless** —
21 trading days has Sharpe SE ≈ 3.5, so a measured Sharpe of +1.0 could
reflect a true value anywhere from -2.5 to +4.5. Don't go live on noise.

## Hard requirements (BLOCK go-live until all green)

### 1. Statistical evidence

- [ ] **≥90 trading days** of paper-trade journal data (≥4.3 calendar months
  including weekends). 30 days is insufficient for any meaningful Sharpe CI.
- [ ] **≥30 days of shadow-decisions logged** for the top 2 candidate variants
  (`momentum_top3_residual_voltgt_v1`, `momentum_top3_residual_v1`). The
  v3.4/v3.6 fixes only landed recently — shadows have minimal data as of
  this checklist's creation.
- [ ] `scripts/strategy_decay_check.py` runs and reports valid Sharpe vs LIVE
  for all shadows. Currently insufficient data; will be useful in ~30 days.
- [ ] `scripts/bootstrap_sharpe_ci.py` 95% CI lower bound > 0.3 on LIVE.
  Currently the CI is `[+0.03, +2.04]` — barely positive. After 90+ days of
  live paper data, the CI should tighten significantly.

### 2. Operational robustness

- [ ] `scripts/chaos_test.py` passes 10/10 fail-safe scenarios. Currently green
  as of v3.17 ship; re-run before go-live.
- [ ] `scripts/account_size_test.py` shows max-error < 5% per position for
  your intended account size. Currently shows whole-share broker drift up to
  2.1% at $10k — confirm fractional-share broker (Alpaca, Robinhood, Fidelity)
  before going live below $50k.
- [ ] Reconciliation runs **every hour during market hours**, not just daily
  (v3.17d adds the hourly cron). Verify in `.github/workflows/`.
- [ ] CI green on master.

### 3. Account setup (REQUIRES MANUAL CONFIRMATION)

- [ ] **Roth IRA opened and funded**. Strategy is fundamentally tax-incompatible
  with taxable accounts: 47% short-term capital gains tax eats ~83% of pretax
  CAGR with 80% monthly turnover. If going live in a taxable account, the
  expected post-tax return drops from ~+19% CAGR to ~+3% CAGR. **Don't.**
- [ ] Broker confirmed to support fractional shares (Alpaca paper does;
  Schwab/Fidelity Roth do; some don't).
- [ ] PDT rule understood: below $25k account equity, you're limited to 3
  day-trades per 5 business days. Strategy is monthly-rebalance so this should
  not bind in normal operation, but emergency exits could violate.
- [ ] API keys for live trading set in GitHub Actions secrets:
  `ALPACA_API_KEY`, `ALPACA_API_SECRET`. Verify `ALPACA_PAPER` is `false`
  in `.github/workflows/daily-run.yml` before flip.

### 4. Independent review

- [ ] **Strategy reviewed by an independent party** (different AI model, human
  with finance background, or both). The v3.x audit was self-graded — I built
  the system AND audited it. Get a second pair of eyes on:
  - Position-sizing logic
  - Cost assumptions (5bp may be optimistic for some scenarios)
  - Risk gates (`risk_manager.py`)
  - Variant promotion criteria
- [ ] Honest baseline numbers acknowledged in writing:
  - Expected CAGR: **+15-20%** (NOT the +74% headline)
  - Expected worst-case drawdown: **-33%**
  - Sharpe 95% CI: `[+0.03, +2.04]`

## Behavioral pre-commit (the part everyone underestimates)

Before going live, write down your answers and don't change them after deploy:

- [ ] **My maximum tolerable drawdown is: ___%**
  Don't pick a number you've never lived through. Most people overestimate
  by 2x. If you've never seen your account drop 20%, write down 20% and
  expect to want to override it.
- [ ] **If I'm down -X% from peak, I will: do nothing for ___ days before any change.**
  This is the most important rule. Panic-selling at the bottom of a -25%
  drawdown is the modal failure mode for retail momentum strategies.
  Backtests don't capture this; only pre-commitment does.
- [ ] **My initial deployment is ___% of intended capital, ramping up over ___ months
  if performance holds.**
  Don't deploy 100% on day one. Recommended: 25% initial, scale to 100% over
  3-6 months if monthly performance stays within backtest expectations.

## Phased rollout plan

### Phase 1: Paper extension (current → +60 days)

Continue paper-trade. During this window:
- Accumulate shadow A/B data on `top3_residual_voltgt_v1` (the strongest
  candidate from v3.16)
- Live through at least one mini-regime-change (any week with SPY -3% or
  VIX > 25)
- Run `chaos_test.py` weekly to catch regression
- Run `bootstrap_sharpe_ci.py` weekly — track how the CI tightens

### Phase 2: Live, 25% sizing (day 90-180)

If all hard requirements green:
- Deploy 25% of intended capital
- Monitor daily for first 2 weeks
- Run reconcile every hour during market hours
- If anything looks weird, halt — don't push through

### Phase 3: Scale to 100% (day 180+)

If 90+ days at 25% sizing match backtest expectations within a 1-Sharpe band:
- Scale to 100%
- Continue weekly degradation check
- Re-baseline expectations every 6 months as more live data accumulates

## What `1-Sharpe band` means in practice

Live Sharpe should be within `[+0.0, +2.0]` of measured-paper Sharpe over each
30-day window. Outside that, something has changed (regime, strategy decay,
implementation bug).

## Monitoring after go-live

These scheduled tasks already exist; verify they email properly:
- `trader-daily-run` (executes strategy)
- `trader-daily-perf-digest` (end-of-day P&L vs benchmark)
- `trader-weekly-degradation-check` (drift monitoring)
- `trader-monthly-walkforward` (param sweep)
- `trader-monthly-dsr-audit` (selection-bias correction)

Add post-go-live:
- Hourly reconciliation (v3.17d)
- Quarterly bootstrap-CI re-baseline
- Annual independent strategy review
