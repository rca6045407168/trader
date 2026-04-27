# CRITIQUE.md — brutal self-assessment

*Written by the same agent that built the system. The point is to surface every structural flaw before live capital is at stake. Read this before you trust any number in CAVEATS.md.*

---

## Executive verdict

What I built today is a **competent prototype** that is **not yet a production trading system**. The strategy logic is academically defensible, the operational surface is well-tested for a 4,000-line codebase, but several load-bearing assumptions are wrong and at least three implementation bugs will cause incorrect behavior in production. The risk-parity weights computed live today are based on a P&L attribution that double-counts P&L between sleeves; the time-exit for bottom-catch positions has a SQL bug that will likely never fire correctly; backtest fills are at month-end-close while live fills are at next-day-open, which silently inflates backtested CAGR by 3-7% per year. A reasonable next step is **NOT to add more strategies**, but to spend the next two weeks fixing what's already broken and hardening the critical path.

The system would still be safe to run on $1-5k of real money under close human supervision. It is **not** safe to run unattended on more than that until the bugs below are fixed.

---

## CRITICAL bugs (wrong trading behavior in production)

### B1. Bottom-catch time-exit will rarely fire

In [`execute.py`](../src/trader/execute.py), `close_aged_bottom_catches()` filters orders with `status = 'submitted'` AND tickers that EVER appeared as a BOTTOM_CATCH decision:

```sql
SELECT DISTINCT ticker, MIN(ts) as opened FROM orders
WHERE side = 'BUY' AND ticker IN (
    SELECT ticker FROM decisions WHERE style = 'BOTTOM_CATCH'
) AND status = 'submitted'
GROUP BY ticker
```

Two flaws:
1. `status = 'submitted'` is the value at order placement. Once the order fills, status doesn't get updated in the journal. Filled orders that should be aged-out by time-exit are still picked up here — OK.
2. `MIN(ts)` returns the *earliest* order for that ticker. If we ever held NVDA via momentum then later via bottom-catch, the function will try to close based on the original momentum buy date — attempting to time-exit a momentum position that's not even being held under the bottom-catch rule.
3. Worse: the function never disambiguates *which* lot is the bottom-catch lot. Alpaca holds shares as one fungible position; closing it will close ANY shares we have in that ticker, including momentum-sleeve shares.

**Impact:** the v0.7 redesign of bottom-catch (NO take, NO trail, time-exit at 20d) silently does NOT work. Bottom-catch positions will sit indefinitely until either the cat-stop fires or the next monthly momentum rebalance happens to also drop them. Expected return contribution from bottom-catch is overstated.

### B2. Sleeve P&L attribution commingles momentum and bottom-catch

In [`risk_parity.py`](../src/trader/risk_parity.py), `compute_sleeve_returns_from_journal()` tags each calendar day as either "bottom_active" (a bottom-catch decision in the prior 20 days) or pure momentum. It then computes monthly returns from these two cohorts and feeds them to the inverse-vol weight calculator.

This is wrong. On a "bot_active" day, the daily portfolio return reflects BOTH the momentum positions still held AND the bottom-catch positions. Attributing 100% of the day's return to bottom-catch dramatically over-weights bottom-catch's apparent contribution — in either direction. If a bottom-catch fired during a great momentum day, bottom-catch looks like a star. If it fired before a bear week, bottom-catch wears the loss.

The risk-parity weights computed from this attribution are therefore garbage once we have any live history. The current 40/60 weights are from priors only and don't suffer this issue — but the moment we accumulate `min_obs=6` months of data, they switch to the broken sample-vol calculation.

**Fix:** compute sleeve P&L per-position. Tag every order with sleeve metadata; when a position closes, attribute the realized P&L to the originating sleeve. Requires a position-level ledger, which we don't have.

### B3. Risk-parity uses MONTHLY priors but DAILY-derived sample vols

The priors `PRIOR_MOMENTUM_VOL_MONTHLY = 0.0631` and `PRIOR_BOTTOM_VOL_MONTHLY = 0.0420` are documented as monthly std deviations. `compute_weights()` uses whatever it gets without unit checks. `compute_sleeve_returns_from_journal()` returns monthly series correctly (via `resample("ME")`), but if anyone wires daily returns into `compute_weights()` directly, the comparison is wrong by a factor of `sqrt(21) ≈ 4.6x`.

Not a current-state bug, but a landmine for the next contributor.

### B4. Backtest assumes month-end-close fills; live trades at next-day-open

The momentum backtest:
```python
weights at end of month T → earn returns from T to T+1
```

Where returns are calculated `monthly.pct_change()` which is close-to-close. So the backtest assumes we sell at month-end close AND buy at month-end close — instantaneous, no slippage.

Live: we generate orders at month-end after close (4:10pm PT scheduled task), Alpaca submits to next-day open (or queues for it). Real fill happens at the OPEN of the first business day of the new month. Empirically the open-vs-prior-close gap on a single-name basis averages 0.5-1.5% absolute, with positive skew on news days.

**Quantified impact:** with 12-month lookback / top-5 / monthly rebalance, average turnover is ~25-40% per rebalance (1-2 names rotate). Each rotation pays the open-vs-close gap once on the buy and once on the sell. At 1% absolute gap per side on 30% turnover = ~60bps per month = **~7% CAGR drag**. Backtest CAGR of 30% becomes real CAGR of ~22-25% from this alone, before survivorship bias and other costs.

### B5. Idempotency check has a race window

In `main.py`:
```python
if any(s["date"] == today_iso for s in today_snaps):
    return {"skipped": True}
```

The daily_snapshot is only written AFTER orders are placed (last step in `main()`). If the orchestrator crashes between order placement and snapshot:
- Orders are pending in Alpaca
- No snapshot exists
- Next run thinks we haven't run today
- Re-runs and submits DUPLICATE orders

This happened today during testing (--force bypassed the check, but the symptom — 5 duplicate orders — was the same root cause: idempotency check fails open).

**Fix:** write a sentinel row to a `runs` table at the START of `main()` with status="started", update to "completed" at end. Idempotency checks `runs` not snapshots.

### B6. Bottom-catch debate Bull/Bear/Risk is unmeasured

Each bottom-catch trade fires three Claude API calls (~9-15 sec total, ~$0.05). For 1397 historical triggers in our backtest, that would be **$70 in API costs** just for the debate. We never measured whether the debate filters out bad trades or just slows down good ones.

If the Risk Manager rejects 50% of trades randomly, expected return drops 50% — with the appearance of "prudent risk management." If it always says BUY, we wasted the latency. We genuinely don't know which.

**Fix:** A/B test — randomize 50% of bottom-catches to skip the debate, compare 30-day forward returns. If debate doesn't add 10%+ to forward returns it's net-negative.

---

## HIGH-severity bugs (wrong P&L attribution / wrong learning)

### B7. No position-level sleeve tagging

Alpaca holds shares fungibly. We have no way to know if 100 shares of NVDA came from momentum or bottom-catch. All P&L attribution downstream is therefore approximate or wrong.

**Fix:** maintain a position-lots table in the journal. Each order writes a lot record with sleeve_id. Closes are attributed FIFO to the sleeve that opened them.

### B8. Sample vol minimum (6 obs) is too small

`compute_weights(min_obs=6)`. Six months of data is barely enough to estimate vol. The standard error of a 6-sample std dev is ~30% of the true value. A weight calculation based on this will swing wildly month-to-month.

**Fix:** require at least 24 monthly obs before switching from priors to sample. Blend gradually: linear weighting from priors to sample over months 24–60.

### B9. Reconciliation is daily-snapshot-based; can miss intraday changes

If a stop-loss fires intraday (because a stock gapped down), the position is gone. But our journal still thinks we hold it. Reconciliation runs at end-of-day and flags the divergence — but we've already missed the chance to act on it.

**Fix:** webhook subscriber for Alpaca order updates. Every fill, every cancel, every stop trigger writes immediately to journal.

### B10. yfinance VIX is delayed; risk-manager decision is stale

`get_vix()` uses yfinance which can lag 15-20 minutes. For the daily orchestrator running at 4:10pm PT this is fine (markets closed by then). But the documented architecture suggests using VIX for vol scaling — if anyone moves the daily run intraday, the VIX figure used will be stale.

### B11. Unit tests don't test the hot path

44 tests pass. Almost all are pure-function tests on small inputs (signal correctness, kill_switch boolean logic, journal SQLite writes). NONE test:
- The full pipeline from `main()` end-to-end with mocked Alpaca
- The risk-parity weights computed from a realistic journal state
- The reconciliation-vs-actual divergence detection
- The aged-bottom-catch logic

A bug in any of these would pass CI today.

### B12. `regression_check.py` thresholds are themselves curve-fit

The baseline thresholds (CAGR ≥ 28%, Sharpe ≥ 1.10, MaxDD between -40% and -20%) were chosen by inspection of the v1.2 backtest. Any code change that improves the strategy by 1bp will pass; any regression smaller than the thresholds is undetected. The thresholds need a tolerance band, not a hard floor.

---

## MEDIUM-severity gaps (operational immaturity)

### B13. The system runs on Richard's laptop

Production trading systems do not run on laptops. Laptops sleep, lose wifi, get closed during sleep, run out of battery. Our scheduled tasks depend on `claude-code` being awake at the cron time. If Richard's laptop is asleep when 4:10 PM PT fires, the daily run is skipped (will retry on next launch, but not at the right time).

**Fix:** containerize the trader, deploy to AWS Lightsail or DigitalOcean droplet ($5-10/mo). Schedule via systemd timers. Richard's laptop becomes a monitoring client, not the runtime.

### B14. SQLite journal is a single point of failure

`data/journal.db` is local to the laptop. If the laptop dies / SSD fails / iCloud doesn't sync / accidental delete, we lose all live performance attribution and audit trail.

**Fix:** PostgreSQL on managed cloud (RDS/Neon/Supabase). Daily logical backups. WAL archiving for point-in-time recovery.

### B15. Kill switch state isn't durable across restarts

The kill switch flag at `/tmp/trader_halt` is on tmpfs — cleared on reboot. If the system is halted then the laptop reboots, the halt is gone. The next scheduled run will trade.

**Fix:** persist halt state in journal table.

### B16. Scheduled tasks need manual permission approval (today's bug)

The test task we ran today hung waiting for Bash permission approval that never came. Production tasks tomorrow will hit the same wall unless pre-approved.

**Fix:** add explicit allowlist in `.claude/settings.local.json` for `Bash(python scripts/*.py)` paths. Or: deploy the trader OUTSIDE the Claude Code harness so no permission system gates it.

### B17. No alerting on failures

If the daily run fails for any reason (API outage, bad data, code bug), no one is notified. The post-mortem agent runs the next morning and may notice gaps in the journal, but "may notice" is not "will notify."

**Fix:** wire `notify()` to actually post to Slack on every error, every kill-switch trip, every reconciliation halt.

### B18. No tax accounting

Monthly rebalance generates short-term capital gains. At Richard's bracket (37% federal + ~10% CA), realized 25% gross becomes ~13% net. The strategy is materially less attractive after tax than the headline number suggests.

**Fix:** run this in a Roth IRA. Track wash-sale violations (32-day rebuy disallowance).

### B19. No circuit breakers on Anthropic API failures

If the Claude API is down (or our key gets rate-limited), the bottom-catch debate fails silently with `print("debate error for {ticker}")` and the trade is skipped. So during an Anthropic outage, the system silently drops the bottom-catch sleeve while continuing momentum. This is *probably* the safer failure mode, but it's not explicitly chosen — it's accidental.

### B20. Backtest doesn't model cash interest

Alpaca pays ~3-4% APY on idle cash via partner banks. With ~60% in cash on average (especially under risk-parity 40/60 priors), idle cash earns ~2-2.5% annual return. The backtest assumes 0% on cash, understating realized total return.

---

## LOW-severity / cosmetic

### B21. yfinance is the only data feed

yfinance is unofficial, rate-limited, and has known bad-tick issues. Production systems use redundant feeds (Polygon as primary, yfinance as fallback, IEX direct as ground truth).

### B22. No model registry / strategy versioning

If we change a parameter (say, lookback_months: 12 → 18) we lose the ability to attribute past performance to the OLD strategy. Production systems version strategies and tag every order with the strategy version that emitted it.

### B23. No A/B / shadow framework

We can't deploy a new strategy variant alongside the live one with $0 capital to validate before promotion. Every change is all-or-nothing, with no safe iteration path.

### B24. Equity-curve attribution doesn't separate alpha from beta

We report "alpha vs SPY" as `our_return - SPY_return`. This is *excess return*, not alpha. True alpha requires a beta calculation: `α = our_return - (β × SPY_return)`. If our β is 1.3, our 17% return vs SPY's 12% is mostly beta, not alpha.

### B25. The 200-day MA regime filter test was incomplete

We tested binary regime filters and concluded they hurt. We did NOT test:
- Continuous regime weighting (e.g. weight by SPY/SPY_MA200 ratio)
- Multi-factor regimes (VIX + SPY + term structure)
- Lower frequency: only act when SPY has been above 200d MA for 60+ consecutive days

The "regime filters don't work" conclusion is overstated.

---

## What we got right

In the spirit of fairness, the things that DO work:

- Walk-forward methodology: correctly held out 2021-2025, found that out-of-sample Sharpe was much lower than in-sample (~40% decay typical). This kept us from deploying an overfit strategy.
- Survivorship-bias quantification: by comparing current top-50 vs 2015-known top-50, we measured the ~15% CAGR inflation. Documented this clearly.
- Bracket exit redesign: empirical test (1397 simulated trades) showed brackets were costing 36% of the bottom-catch edge. We removed them.
- Operational scaffolding: 9-layer risk manager + 6-trigger kill switch + 38 unit tests on safety code + reconciliation script.
- Documentation: PAPER.md, DEPLOY.md, CAVEATS.md, README.md — the system is at least intelligible to a future contributor.

---

## Triage — what to fix first

If the goal is "safe to run on $5k of real money this month," fix in this order:

1. **B1** (bottom-catch time-exit doesn't work) — fix the SQL or accept that bottom-catches hold indefinitely
2. **B5** (idempotency race) — add a `runs` table
3. **B16** (scheduled tasks need permission) — either pre-approve permissions or deploy outside Claude Code harness
4. **B17** (no alerting) — wire `notify()` to a real Slack webhook
5. **B11** (no end-to-end test) — write at least one mock-Alpaca pipeline test

If the goal is "safe to run autonomously on $50k+," the entire `MEDIUM` section becomes load-bearing. Plan: 4-6 weeks of deployment work before more strategy work.

If the goal is "safe to run on $500k+," you need the full v3.0 architecture in [ARCHITECTURE.md](ARCHITECTURE.md). At that scale, this codebase should be considered a *prototype that informs* the production rewrite — not the production system itself.

---

*Last updated 2026-04-26. Critique is incomplete by definition; submit additions as PRs.*
