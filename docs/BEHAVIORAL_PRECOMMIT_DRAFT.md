# Behavioral Pre-Commit (DRAFT — review and edit before signing)

**Pre-filled with sensible defaults based on the v3.x audit findings. Review,
edit any number to fit your actual psychology, then sign.**

---

## My drawdown tolerance

> The maximum drawdown I can sit through without panic-selling is: **-25%**

Default rationale: backtest PIT-honest worst-DD is -33%. Setting tolerance
slightly below worst-case (-25%) means if -33% hits, you'll be 8pp deeper
than your stated comfort. Better to know that in advance.

If you've never sat through a 25% drop in real money, **you should consider
setting this lower (-15% or -20%)** and accepting that the strategy may
trigger your halt mid-drawdown.

**EDIT THIS NUMBER if -25% feels wrong.**

---

## My response when in drawdown

> If my account is down -X% from peak, I will:
> **do nothing for 5 trading days before any change.**

Default rationale: 5 days is the minimum cooling-off period documented
in v3.13 strategy decay literature. Long enough to let V-shape recoveries
resolve. Short enough that you can intervene if a real systemic problem
surfaces.

**Increase to 7 or 10 days if you want stronger guardrails.**

---

## My ramp plan

> My initial deployment is **25% of intended capital**, ramping to
> **100%** over **6 months** if performance holds within band.

Default rationale: 25% means if the strategy has unexpected -50% loss in
month 1, you lose 12.5% of intended capital — survivable. Scaling over
6 months gives time for the bootstrap CI on Sharpe to tighten.

Specific schedule:
- **Day 1:** 25% of intended capital deployed
- **Day 30:** review SPY-relative; if within `[-5pp, +5pp]`, scale to 50%
- **Day 90:** review again; if within band, scale to 100%
- **If down >10pp vs SPY at any review:** halt scaling, paper-trade only
- **If up >10pp vs SPY at any review:** still scale on schedule (don't FOMO)

---

## My override rules

I commit to these rules. Violating them = I'm trading against the system.

- [✓] **No override during a drawdown until 5-day cooling-off period passes.**
- [✓] **No override based on news.** "I read this thing about [stock]" is not a reason to override the rebalance.
- [✓] **No override based on social media / Twitter.** During my own drawdowns, I will not read financial Twitter.
- [✓] **No "averaging down" by adding capital during drawdowns.** Pre-set contribution schedule only.
- [✓] **If I want to override, I must:**
  1. Wait 24 hours
  2. Write down the override + reason in `docs/OVERRIDE_LOG.md`
  3. Either change the code (commit + push) or follow the system — never both.

---

## My halt / resume protocol

**If something looks broken (not just losing money):**

```bash
# Halt both workflows
bash scripts/halt.sh

# Investigate, fix in code, push.

# Resume:
bash scripts/resume.sh
```

I will practice this once during paper phase so it's muscle memory.

---

## My monitoring discipline

- **Daily:** Read the perf-digest email (1 minute). NO Alpaca login.
- **Weekly (Sunday 6pm PT):** Run `python scripts/three_numbers.py`. 30 seconds.
- **Monthly:** Run `python scripts/regime_stress_test.py` + `python scripts/strategy_decay_check.py`.
- **Quarterly:** Run `python scripts/run_optimizer.py` (walk-forward).
- **Annually:** Independent strategy review by 2nd party.

---

## What I will NOT do

- [✓] Open Alpaca multiple times a day to check P&L
- [✓] Change LIVE variant within 30 days of a Sharpe drop
- [✓] Add new shadow variants without a specific testable hypothesis
- [✓] Compare my returns to specific stocks ("I would have made more in NVDA")
- [✓] Tell anyone the dollar amounts (talking about it makes you trade more)
- [✓] Trade against the strategy ("I have a feeling about this one")

---

## Honest expectations (re-read this during drawdowns)

These are the PIT-honest forward-looking numbers from v3.x audit:

- **Expected CAGR:** +15-20% (NOT +74% headline)
- **Expected Sharpe:** ~+0.96
- **Expected worst drawdown:** -33%
- **Expected excess over SPY:** +2-4pp/yr (per AQR live momentum funds)
- **Probability strategy has positive edge:** 88-98% (varies by methodology)
- **Probability of -33% drawdown in any 12-month period:** ~10-15%

Compounded over 20-30 years, +2-4pp excess over SPY = ~30-50% MORE wealth
than buy-and-hold. Real. Replicable. Just not life-changing.

If you're hoping for life-changing returns, this strategy won't deliver them.
For that you'd need leverage, options, crypto, or VC — different games with
different risk profiles. Each tested in v3.x audit; none cleanly compatible
with a Roth IRA at $100k.

---

## Signed and dated

I commit to these rules.

Signature: _______________________

Date: _______________________

(Re-sign annually as part of strategy review.)

---

## NEXT STEP

After signing this draft (edit the numbers above to fit your actual
psychology, then save as `docs/BEHAVIORAL_PRECOMMIT.md`):

1. Print it. Tape next to monitor.
2. Don't deploy real capital until ALL of the following are true:
   - This document is signed
   - `docs/ROTH_IRA_SETUP.md` checklist is complete (next file)
   - `python scripts/go_live_gate.py` shows 9/9 automated gates passing
   - At least 60 trading days of paper-trade have elapsed

The system will email you (`scripts/readiness_monitor.py`) when the
automated gates flip from yellow to green.
