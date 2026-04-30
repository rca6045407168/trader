# Richard's Action Items — Pre-Live Deployment

Single doc with everything you need to do before deploying real capital.
The system handles automation; you handle the things only you can do.

---

## 🟢 What the system handles automatically (already wired)

You don't need to do anything for these. They run on cron:

- **Daily-run** Mon-Fri 21:10 UTC: rebalances paper account
- **Hourly-reconcile** Mon-Fri 14-20 UTC: catches Alpaca↔journal drift
- **Weekly-digest** Sunday 00:00 UTC: emails performance summary
- **Readiness monitor** (post-daily-run, NEW): emails when go-live gate flips green
- **Drawdown alerts** (NEW): emails at -5%, -10%, -15% from peak
- **103 unit tests** + spec test catches LIVE drift

You'll get:
- Daily perf-digest email
- Drawdown alerts only when triggered (won't spam)
- Go-live readiness email when 9/9 gates pass (one-shot)
- Halt alerts only on real failure

---

## 🟡 What you need to do (in order, NO RUSH)

### Action 1 — Fill out behavioral pre-commit (~30 minutes, do this WEEK 1)

The single most important pre-deployment step.

```bash
cd /Users/richardchen/trader
cp docs/BEHAVIORAL_PRECOMMIT_DRAFT.md docs/BEHAVIORAL_PRECOMMIT.md
# Edit docs/BEHAVIORAL_PRECOMMIT.md — the DRAFT has sensible defaults
# Adjust the numbers (drawdown tolerance, response window, ramp plan)
# Sign + date at the bottom
git add docs/BEHAVIORAL_PRECOMMIT.md
git commit -m "behavioral pre-commit signed by Richard"
git push
```

This locks your decision criteria BEFORE you have skin in the game. The
biggest risk to systematic strategies is operator panic-overriding mid-flight.
Pre-commitment removes that vector.

**DO NOT SKIP THIS.** I've seen 30 versions of testing and the math; what
will kill you is your own behavior in a -25% drawdown, not the strategy.

### Action 2 — Open Roth IRA brokerage account (~30 minutes online + 1-3 day wait)

See `docs/ROTH_IRA_SETUP.md` for step-by-step.

**Recommended: Alpaca Roth IRA** (zero code migration).

This is just account-opening. You're NOT deploying capital yet — just having
the account ready when the readiness gate passes.

### Action 3 — Get independent strategy review (~1 hour, can be a different AI model)

Spawn a second-opinion review on the v3.x strategy. The v3.x audit was
self-graded; that's not enough.

Suggested prompt to Claude (different session) or ChatGPT or another model:

> I have a personal trading system on Alpaca paper. The LIVE strategy is
> top-15 stocks weighted by 12-month momentum, 80% gross allocation,
> monthly rebalance. PIT-honest backtest: Sharpe +0.95, CAGR +16%, worst
> DD -31%. Bootstrap 95% CI on Sharpe is [+0.03, +2.04]. Code is at
> github.com/rca6045407168/trader (master branch).
>
> Read the codebase critically. Identify:
> 1. Methodology bugs that survive (look-ahead bias, transaction-cost
>    underestimation, rebalance off-by-one)
> 2. Operational risks unique to going live (kill-switch coverage,
>    reconciliation gaps, single points of failure)
> 3. Where the +0.95 Sharpe claim is statistically suspect
> 4. What you'd want fixed BEFORE deploying $25k+ of real capital
>
> Be adversarial. The goal is to find what's wrong, not to validate
> what's right. Reply in <800 words.

Save the response in `docs/INDEPENDENT_REVIEW_2026.md` and address any
material findings.

### Action 4 — Wait for paper-test data (~60 trading days from 2026-04-29)

The system will email you when `python scripts/go_live_gate.py` shows
9/9 automated gates passing. Currently 7/9 — the 2 failing are statistical
data gates (≥90 paper days, ≥30 shadow-decision days). They'll auto-resolve
as time passes.

Do NOTHING during this window. Just live your life. The system runs itself.

### Action 5 — When all 4 above are done, deploy 25% (10 minutes)

Final pre-flight:
```bash
cd /Users/richardchen/trader
python scripts/go_live_gate.py  # must show 9/9 green
ls docs/BEHAVIORAL_PRECOMMIT.md  # must exist (signed)
ls docs/INDEPENDENT_REVIEW_2026.md  # must exist
```

If all three checks pass:
1. Follow `docs/ROTH_IRA_SETUP.md` Phase 4-6 to flip to LIVE keys
2. Initial deployment: **25% of intended capital ONLY**
3. Set 30-day calendar reminder for first review

### Action 6 — 30-day review (10 minutes)

```bash
python scripts/three_numbers.py    # excess CAGR vs SPY, worst DD, Sharpe
python scripts/spy_relative_dashboard.py  # detailed view
```

If excess CAGR > -5pp vs SPY: scale to 50%
If excess CAGR > 0pp vs SPY: scale to 100% by day 90
If excess CAGR < -5pp vs SPY: HALT, go back to paper, investigate

---

## 🔴 What I (the AI) cannot do for you

- Open the brokerage account (KYC requires you)
- Sign the behavioral pre-commit (your psychology, your numbers)
- Get the independent review (need a 2nd party)
- Decide your risk tolerance
- Authorize real-capital deployment
- Override the system mid-drawdown (you have to commit to NOT doing this)

Everything else I've automated.

---

## 📅 Timeline

| Day | What happens |
|---|---|
| Today (2026-04-30) | v3.42 LIVE flip executes tonight 21:10 UTC |
| Day 1 (tomorrow) | Verify Alpaca shows 15 holdings; account healthy |
| Days 2-30 | Live the strategy. Daily perf-digest. NO Alpaca login. |
| Day 30 | Open Roth IRA (Action 2) |
| Day 60 | Get independent review (Action 3) |
| Day 60-90 | Sign behavioral pre-commit (Action 1) |
| Day 90 | Readiness monitor emails when go-live gate is 9/9 |
| Day 90+ | Deploy 25% of intended capital |
| Day 120 | First 30-day review, scale to 50% if within band |
| Day 180 | Second review, scale to 100% if within band |

By **end of October 2026** you should be running the full live strategy
with real capital, having lived through at least one drawdown event in
paper mode and pre-committed your behavioral rules.

---

## 🎯 The single biggest decision

**Do not deploy real capital without this entire checklist complete.**

The +5% in 2 days that we just saw on paper is meaningless statistically.
The honest forward expectation is +15-20% CAGR with -33% worst-DD. That
means you should EXPECT a -33% drawdown at some point in the first 12-24
months of live trading. Pre-commitment is the only thing standing between
you and panic-selling at the bottom.

If at any point this seems too disciplined, re-read v3.31 (the meta-finding)
and v3.36 (CPCV invalidating the HMM "winner"). The discipline isn't
conservatism — it's what's empirically required to extract any edge at
retail scale.
