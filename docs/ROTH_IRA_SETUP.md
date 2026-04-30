# Roth IRA Setup Checklist

Pre-deployment infrastructure. Complete this BEFORE going live with real
capital. Each step is required.

---

## Why Roth IRA specifically (not taxable)

Per the v3.x audit, this strategy has **80% monthly turnover**. In a taxable
account at a 47% short-term capital gains rate (federal + state for high
earners), you lose **~83% of pretax CAGR to taxes**:

  Pretax CAGR: +19% → After-tax CAGR: ~+3%

In a Roth IRA: **0% tax on gains.** Pretax CAGR = After-tax CAGR.

**A 16pp/yr difference, compounded for 30 years, is the difference between $1.7M
and $5.5M on $100k starting capital.** Not deploying in a Roth IRA is the
single biggest mistake you could make with this strategy.

---

## Broker selection

You need a broker that:
- ✓ Is approved for Roth IRA
- ✓ Supports fractional shares (so a 5%-of-$25k position can buy partial shares of $500+ stocks)
- ✓ Has zero or near-zero commissions
- ✓ Has a reliable API for systematic trading
- ✓ Won't restrict your account for "frequent trading" (Roth IRA is exempt from PDT but some brokers have internal limits)

### Recommended brokers (in order of fit)

**1. Alpaca** (you're already using paper here)
- ✓ Roth IRA supported (`alpaca.markets/algorithmic-trading-roth-ira`)
- ✓ Fractional shares
- ✓ $0 commissions
- ✓ Same API you're using for paper — zero migration work
- ⚠ Smaller broker, less name recognition; account insurance via SIPC standard

**2. Interactive Brokers**
- ✓ Roth IRA supported, decade+ track record
- ✓ Fractional shares
- ✓ $0 commissions on US stocks
- ✓ Robust API (different from Alpaca — would need migration work)
- ⚠ Steeper learning curve

**3. Fidelity**
- ✓ Roth IRA supported, large institutional reputation
- ✓ Fractional shares
- ✓ $0 commissions
- ⚠ API access requires Fidelity API agreement; less developer-friendly than Alpaca/IBKR
- ⚠ Would require migration work + ongoing API maintenance

**My recommendation: Alpaca Roth IRA.** Zero code migration, same API,
already familiar. Insurance is the same SIPC level.

---

## Step-by-step setup

### Phase 1: Open the account (you do this — ~30 min)

- [ ] Visit `alpaca.markets/algorithmic-trading-roth-ira` (or chosen broker)
- [ ] Open Roth IRA account application
- [ ] Provide: SSN, employer, 2 forms of ID, banking info
- [ ] Wait 1-3 business days for approval

### Phase 2: Fund the account (~5 min after approval)

- [ ] Link external bank account (typically requires 2 micro-deposit verifications, takes 2 days)
- [ ] **2026 Roth IRA contribution limit: $7,000** ($8,000 if age 50+)
- [ ] Initiate contribution: **start with $25,000 if you have prior years' contribution room** OR start with $7,000 and add later
- [ ] Wait for funds to settle (typically 3-5 business days)

### Phase 3: API key setup (you do this — ~10 min)

- [ ] In Alpaca dashboard, go to "API Keys" section
- [ ] Generate new LIVE (not paper) API keys
- [ ] **DO NOT commit these to git.** Add to `.env` file (which is in `.gitignore`)
- [ ] In `.env`:
  ```
  ALPACA_LIVE_API_KEY=PK...
  ALPACA_LIVE_API_SECRET=...
  ```
- [ ] Update `src/trader/config.py` to support live keys (currently uses paper keys)

### Phase 4: GitHub Actions secrets setup (~5 min)

The cron-based daily-run uses GitHub Actions. To trade real money, the
secrets need to be flipped:

- [ ] In GitHub repo Settings → Secrets and variables → Actions:
  - [ ] Rename current `ALPACA_API_KEY` → `ALPACA_PAPER_API_KEY` (preserves paper mode)
  - [ ] Add new `ALPACA_LIVE_API_KEY` with the live key
  - [ ] Add new `ALPACA_LIVE_API_SECRET`
- [ ] In `.github/workflows/daily-run.yml`:
  - [ ] Change `ALPACA_PAPER: "true"` → `ALPACA_PAPER: "false"`
  - [ ] Update `ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}` → `${{ secrets.ALPACA_LIVE_API_KEY }}`

**DO NOT do this step until ALL other gates pass.**

### Phase 5: Verify live readiness (~10 min)

- [ ] `python scripts/go_live_gate.py` shows 9/9 automated gates passing
- [ ] `docs/BEHAVIORAL_PRECOMMIT.md` is signed (saved from `_DRAFT.md` after edit)
- [ ] Independent strategy review completed (different AI model or human reviewer)
- [ ] At least 60 trading days of paper-trade journal data accumulated
- [ ] `python scripts/three_numbers.py` shows excess CAGR over SPY > 0
- [ ] `python scripts/strategy_decay_check.py` shows no shadow significantly outperforms LIVE

### Phase 6: Initial 25% deployment (only after all above complete)

- [ ] Deploy 25% of intended capital — DO NOT exceed this amount
- [ ] First daily-run after Phase 4 will execute the strategy with LIVE keys
- [ ] Tomorrow morning: verify orders filled at expected prices, account state matches journal
- [ ] Set 30-day calendar reminder: review SPY-relative performance, decide on scaling

---

## Important caveats

1. **Contribution limits are HARD CAPS.** $7,000/yr for 2026. If you only have $7k
   in the account, you cannot deploy $25k until you have years of contributions.
   You may need to roll over an old 401(k) or IRA into the Roth (taxable event!).

2. **Withdrawal penalties.** Roth IRA contributions can be withdrawn anytime, but
   gains can't be withdrawn before age 59½ without 10% penalty (with limited exceptions).
   This is RETIREMENT money. Don't deploy capital you'll need before then.

3. **PDT rule exception.** Roth IRA accounts are NOT subject to the $25k pattern day
   trader rule because Roth IRAs cannot day-trade by IRS rules. Strategy is monthly
   rebalance so this doesn't bind anyway.

4. **No options writing in some Roth setups.** If you want to add the v3.43 barbell
   sleeve later, verify Alpaca Roth IRA supports buying call options. (It does, but
   selling naked options is restricted.)

5. **Wash-sale rules don't apply to Roth IRA** because there's no taxable event.
   You can sell at a loss and rebuy immediately — no IRS issue.

---

## Onboarding sequence summary

```
Day 0: Open Alpaca Roth IRA account
Day 1-3: Account approved
Day 3-5: Fund account ($7k-$25k)
Day 5-7: Deposit settles, ready to trade
Day 8: Set up live API keys in .env + GitHub secrets
Day 9: Wait — DO NOT FLIP ALPACA_PAPER FLAG YET
   ↓ Continue paper-trading via existing setup
   ↓ Wait for go_live_gate.py to show 9/9 green (≥60 paper days)
Day 60+: Sign BEHAVIORAL_PRECOMMIT.md
Day 60+: Flip ALPACA_PAPER to "false" in workflow
Day 60+: Next daily-run executes with REAL money at 25% sizing
Day 90+: Review, scale to 50% if within band
Day 180+: Review, scale to 100% if within band
```

The patient version of this is the version that doesn't blow up.
