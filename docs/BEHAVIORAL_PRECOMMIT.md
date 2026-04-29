# Behavioral Pre-Commit Template

**Fill this out BEFORE going live and don't change it after deploy.**
Print it. Tape it next to your monitor. The biggest risk to systematic
strategies isn't the strategy — it's the operator panic-overriding it
during drawdowns most of which last 1-3 weeks.

---

## My drawdown tolerance

> The maximum drawdown I can sit through without panic-selling is: **___%**

Honest version of the question: if my $___ account drops to $___, what is
my actual gut-feel response?

Don't pick a number you've never lived through. PIT-honest backtest worst-DD
is **-33%**. If you've never seen a 30% drop, write down 20% and expect to
want to override it.

---

## My response when in drawdown

> If my account is down -X% from peak, I will:
> **do nothing for ___ trading days before any change.**

Recommended minimum: 5 trading days.

Why: V-shape recoveries are common in momentum drawdowns. Panic-selling
at the bottom of a -25% drawdown is the modal failure mode for retail
momentum investors. The cooling-off period gives you a chance to
make decisions when you're not in flight-or-fight mode.

---

## My ramp plan

> My initial deployment is **___% of intended capital**, ramping to
> **100%** over **___ months** if performance holds within band.

Recommended:
- 25% on day 1
- 50% if 30-day SPY-relative tracking is within band
- 100% if 90-day SPY-relative tracking is within band

"Within band" = excess CAGR over SPY is in `[-5pp, +5pp]` over the period
(i.e., not catastrophically off backtest expectations).

---

## My override rules

I commit to these rules. Violating them = I'm trading against the system,
which means I should either change the system in code OR not override
mid-flight.

- [ ] **No override during a drawdown until the cooling-off period passes.**
  If down -X% from peak, no change for ___ trading days.

- [ ] **No override based on news.** "I read this thing about AAPL" is not
  a reason to override the rebalance. The strategy is systematic. News is
  noise to a momentum strategy.

- [ ] **No override based on social media / Twitter.** During my own
  drawdowns, I will not read financial Twitter. Survivorship bias
  goes to 11 there.

- [ ] **No "averaging down" by adding capital during drawdowns.** My
  contribution schedule is pre-set. Any capital additions follow that
  schedule, NOT my emotional state.

- [ ] **If I want to override, I must:**
  1. Wait 24 hours
  2. Write down the override + the reason in `docs/OVERRIDE_LOG.md`
  3. Either change the code (commit + push) or follow the system —
     never both.

---

## My halt / resume protocol

**If something looks broken (not just losing money):**

```bash
# Halt both workflows
gh workflow disable trader-daily-run
gh workflow disable trader-hourly-reconcile

# Or use the wrappers (safer):
bash scripts/halt.sh

# Investigate, fix in code, push.

# Resume:
bash scripts/resume.sh
```

I will practice this once during paper phase so it's muscle memory.

---

## My monitoring discipline

**Daily:** Read the perf-digest email (1 minute). No login to Alpaca.

**Weekly (Sunday 6pm):** Run `python scripts/three_numbers.py`. Read
the 3 numbers. If all green, close the terminal. If alert, escalate.

**Monthly:** Run `python scripts/regime_stress_test.py`. Run
`python scripts/strategy_decay_check.py`. Read the LIVE-vs-shadow
comparison. Decide if any shadow has earned promotion consideration.

**Quarterly:** Run `python scripts/run_optimizer.py` (walk-forward).
Compare deployed params to recommendation. Only change if recommendation
beats deployed by ≥0.2 Sharpe with low decay.

**Annually:** Independent strategy review by 2nd party (different model
or human with finance background).

---

## What I will NOT do

- [ ] Open Alpaca multiple times a day to check P&L
- [ ] Change LIVE variant within 30 days of a Sharpe drop
- [ ] Add new shadow variants without a specific testable hypothesis
- [ ] Compare my returns to specific stocks ("I would have made more in NVDA")
- [ ] Tell anyone the dollar amounts (talking about it makes you trade more)
- [ ] Trade against the strategy ("I have a feeling about this one")

---

## Signed and dated

I commit to these rules.

Signature: _______________________

Date: _______________________

(Re-sign annually as part of strategy review.)
