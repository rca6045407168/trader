# Roth IRA Setup Checklist

**v3.48 CORRECTION (2026-05-02):** Earlier version of this doc recommended
opening a Roth IRA directly with Alpaca. **That was wrong.** Per Alpaca
support: "As of September 2024, IRA accounts are only available for Broker
API clients" — i.e., Alpaca only sells IRAs to fintech partners (Robinhood,
SoFi, etc.), not to retail individuals directly.

**Recommended broker is now Public.com.** Public has: direct retail Roth IRA,
fractional shares, official Python Trading API, $0 API access. Closest
match to Alpaca's developer-friendly pattern that's actually open to retail.

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
- ✓ Direct to retail (not via fintech partner)
- ✓ Supports fractional shares (so 5%-of-$25k can buy partial shares of $500+ stocks)
- ✓ Has zero or near-zero commissions
- ✓ Has a reliable API for systematic trading
- ✓ Won't restrict your account for "frequent trading" (Roth IRA is exempt from PDT but some brokers have internal limits)

### Recommended brokers (in order of fit, post-correction)

**1. Public.com — recommended**
- ✓ Roth IRA direct to retail (https://public.com)
- ✓ Fractional shares
- ✓ $0 API access (`pip install publicdotcom-py`)
- ✓ Official Python SDK with API key auth (similar pattern to Alpaca)
- ✓ Order types: MARKET, LIMIT, STOP, STOP_LIMIT, with TIF=DAY/GTC
- ⚠ Need to migrate ~`src/trader/execute.py` from Alpaca SDK to Public SDK (estimated 1-2 days work — see migration doc)
- ⚠ Newer broker, smaller AUM than IBKR/Fidelity; verify SIPC coverage
- ⚠ Has a 1% IRA contribution-match program (separate from API)

**2. Interactive Brokers — fallback if Public doesn't work out**
- ✓ Roth IRA direct to retail, decade+ track record
- ✓ Fractional shares on all US stocks/ETFs
- ✓ $0 commissions on US stocks
- ✓ Robust API (TWS Python or Web API)
- ⚠ Heavier API migration than Public (different pattern entirely from Alpaca)
- ⚠ Steeper UI / onboarding learning curve

**3. Charles Schwab — only if you're already a Schwab customer**
- ✓ Roth IRA, large institutional reputation
- ✓ Fractional shares ("Schwab Stock Slices") — but ONLY on S&P 500 names
- ⚠ Schwab API exists but is enterprise-focused; significant migration work
- ⚠ S&P 500-only fractionals limit our universe

**Why NOT Alpaca for this:** They don't sell Roth IRAs to retail. Period.
The earlier doc was wrong on this point. The Alpaca paper account stays
useful for ongoing testing — we just can't deploy live capital there.

---

## Step-by-step setup (Public.com path)

### Phase 1: Open the account (you do this — ~15 min)

- [ ] Visit https://public.com (NOT public.com/api — that's the developer
      portal). Click "Open account" → "Retirement" → "Roth IRA"
- [ ] Application asks for:
   - SSN (full)
   - Date of birth
   - Government-issued ID photo (driver's license / passport)
   - Address with proof of residency
   - Employer + employment status
   - Tax filing status
   - Trusted contact info
- [ ] Wait 1-3 business days for approval

### Phase 2: Fund the account (~5 min after approval)

- [ ] Link external bank account (typically requires 2 micro-deposit verifications, takes 2 days)
- [ ] **2026 Roth IRA contribution limit: $7,000** ($8,000 if age 50+)
- [ ] Initiate contribution: **start with $25,000 if you have prior years' contribution room** OR start with $7,000 and add later
- [ ] Wait for funds to settle (typically 3-5 business days)

### Phase 3: API key generation (you do this — ~10 min after funding)

- [ ] Log in to public.com web interface
- [ ] Navigate to: **Account Settings → Security → API**
- [ ] Click "Create personal access token" / "Get API Keys"
- [ ] Note the **API secret key** AND your **account number** (both are required)
- [ ] **DO NOT commit these to git.** Add to `.env`:
   ```
   PUBLIC_API_SECRET=<from public.com>
   PUBLIC_ACCOUNT_NUMBER=<from public.com>
   ```
- [ ] `.env` is already in `.gitignore` — verify before saving

### Phase 4: Code migration (I do this — ~1-2 days work, see MIGRATION_ALPACA_TO_PUBLIC.md)

The trader currently uses `alpaca-py`. Migration to `publicdotcom-py`
requires changes in:
- `src/trader/execute.py` — order placement / position fetching
- `src/trader/config.py` — env vars
- `src/trader/reconcile.py` — actual-position fetching
- `.github/workflows/daily-run.yml` — secrets + env

Migration is mostly mechanical (similar API surface), tested via paper
account first. Would NOT touch live until 90+ paper days complete.

### Phase 5: GitHub Actions secrets setup (~5 min)

- [ ] In GitHub repo Settings → Secrets and variables → Actions:
  - [ ] Keep `ALPACA_API_KEY` / `ALPACA_API_SECRET` for paper-test continuation
  - [ ] Add new `PUBLIC_API_SECRET` and `PUBLIC_ACCOUNT_NUMBER` (live)
- [ ] In `.github/workflows/daily-run.yml`:
  - [ ] Add new `BROKER` env var: `paper` (default) or `public_live`
  - [ ] Conditional broker init based on env var

**DO NOT flip BROKER=public_live until ALL go-live gates pass.**

### Phase 6: Verify live readiness (~10 min)

- [ ] `python scripts/go_live_gate.py` shows 9/9 automated gates passing
- [ ] `docs/BEHAVIORAL_PRECOMMIT.md` is signed (saved from `_DRAFT.md` after edit)
- [ ] Independent strategy review completed (different AI model or human reviewer)
- [ ] At least 60 trading days of paper-trade journal data accumulated
- [ ] `python scripts/three_numbers.py` shows excess CAGR over SPY > 0
- [ ] `python scripts/strategy_decay_check.py` shows no shadow significantly outperforms LIVE
- [ ] **NEW v3.48:** Public.com migration tested via paper-equivalent first (Public has paper trading too, supposedly)

### Phase 7: Initial 25% deployment (only after all above complete)

- [ ] Deploy 25% of intended capital — DO NOT exceed this amount
- [ ] First daily-run after Phase 5 will execute the strategy with PUBLIC live keys
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

4. **No options writing in some Roth setups.** Verify Public.com Roth IRA supports
   buying call options before assuming v3.43 barbell sleeve works there.

5. **Wash-sale rules don't apply to Roth IRA** because there's no taxable event.
   You can sell at a loss and rebuy immediately — no IRS issue.

6. **Public.com is newer than IBKR/Schwab.** SIPC coverage is standard but the
   firm's longevity is shorter. If broker-failure risk concerns you, prefer
   Interactive Brokers despite the heavier API migration.

---

## Onboarding sequence summary (corrected)

```
Day 0: Open Public.com Roth IRA account
Day 1-3: Account approved
Day 3-5: Fund account ($7k-$25k)
Day 5-7: Deposit settles, ready to trade
Day 8: Generate API keys; add to .env + GitHub secrets
Day 9-15: I migrate execute.py / reconcile.py / config.py to publicdotcom-py;
         test extensively via Alpaca paper continuation in parallel
Day 15+: Continue paper-trading via Alpaca; PUBLIC live still NOT armed
   ↓ Wait for go_live_gate.py to show 9/9 green (≥60 paper days from today)
Day 60+: Sign BEHAVIORAL_PRECOMMIT.md
Day 60+: Flip BROKER=public_live in workflow
Day 60+: Next daily-run executes with REAL money at 25% sizing
Day 90+: Review, scale to 50% if within band
Day 180+: Review, scale to 100% if within band
```

The patient version of this is the version that doesn't blow up.
