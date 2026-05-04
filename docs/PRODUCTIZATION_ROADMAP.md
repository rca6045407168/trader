# Productization roadmap — turning this into a sellable AI trading agent

*Generated 2026-05-04 (v3.64.0). Companion to BEST_PRACTICES.md.*

This codebase started as Richard's personal Roth-IRA experiment. It's grown
into something that could be productized as a hosted AI trading-agent
platform. **This doc maps what we'd need to build.** Not a sales deck;
honest engineering spec.

---

## What's already shipped (v3.64.0 baseline)

✅ **HANK** branded persona — system prompt + UI title + voice discipline
✅ **Per-symbol AI summary** on drill-down (Bloomberg-style)
✅ **Email alerts** wired (`SMTP_USER` + `SMTP_PASS` env)
✅ **Compliance audit log** for every LLM call (table `llm_audit_log`,
   per-context cost tracking, CSV export for regulator request)
✅ **Self-evaluating post-mortem** runs nightly via prewarm
✅ **Slack notifications** + ntfy.sh option
✅ Strategy Lab w/ 31 strategies, refutation categorization, plain-English
   descriptions
✅ Backtest infrastructure (walk-forward, parameter sensitivity, stress
   test, refutation analysis, multi-source earnings calendar)
✅ Honest verdict transparency (REFUTED / VERIFIED / CALMAR_TRADE flags)

---

## What's NOT shipped — Tier C deferred items

These need architectural decisions BEFORE coding. Listed with the actual
specs you'd hand to an engineer.

---

### Item #4: Multi-tenant + auth + hosted deployment

#### Why this is hard

Going from "Richard runs this in Docker on his laptop" to "100 paying users
each with their own broker accounts and Roth IRAs" is not a refactor.
It's a re-architecture across:

- **Per-user isolation:** each user's trades, positions, journal must be
  segregated. Today everything writes to `data/journal.db` — one shared
  SQLite. Need: per-user database OR a multi-tenant schema with
  `user_id` on every table.
- **Per-user broker credentials:** Alpaca keys are in `.env`. Need a
  vault (HashiCorp Vault, AWS Secrets Manager, or just per-user encrypted
  blobs in the user-DB). NEVER cleartext at rest.
- **Auth:** there is none. Need: OAuth2 (Auth0 / Clerk / WorkOS) or
  email-magic-link (Supabase Auth) or password+MFA (Cognito).
- **Hosting:** runs as Docker compose locally. Need: managed runtime
  (Fly.io / Render / GCP Cloud Run) + managed DB (Cloud SQL / Neon).
- **Billing:** Stripe subscription. $X/mo per user; tier by AUM.
- **Multi-broker support:** Alpaca only today. Need: IBKR (much harder
  API), Schwab/TDA (acquired), Robinhood (no docs).

#### Recommended architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Cloudflare / Fastly                      │
│                  (CDN + DDoS + WAF + auth)                   │
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────▼────────┐         ┌──────────────────┐
        │ Streamlit (multi)│◄────────┤ Auth0 / Supabase │
        │  Cloud Run       │         │  user identity   │
        └────────┬────────┘         └──────────────────┘
                 │
        ┌────────▼─────────────┐
        │  Per-user DB shard   │  ── PostgreSQL (Neon / Cloud SQL)
        │  schema: trader_{uid} │     1 schema per user; share connection pool
        └────────┬─────────────┘
                 │
        ┌────────▼─────────────┐
        │  Job runner (Celery /│  ── Cloud Tasks
        │  Sidekiq)            │     daily rebalance, backups, postmortem
        └────────┬─────────────┘
                 │
        ┌────────▼─────────────┐         ┌────────────────┐
        │  Broker adapters     │◄────────┤ Vault (Secrets │
        │  Alpaca / IBKR / etc │         │ Manager)       │
        └──────────────────────┘         └────────────────┘
```

#### Effort estimate

- Auth + per-user DB schema: 2 weeks
- Hosting infrastructure (CDN + Cloud Run + DB + secrets): 1 week
- Stripe billing + subscription tiers: 1 week
- IBKR adapter: 2 weeks (Alpaca's API is friendlier)
- Migration scripts for existing single-tenant install: 1 week
- Pen-test + SOC2 prep: 4 weeks (if pursuing institutional customers)

**Total: ~3 months of focused engineering for an MVP. ~6 months for SOC2.**

#### Pricing model (reference)

| Tier | Price | AUM cap | Features |
|---|---|---|---|
| Personal | $19/mo | $50K | LIVE momentum, HANK chat, email alerts |
| Pro | $99/mo | $500K | + Multi-sleeve, custom strategies, options |
| Family Office | custom | $5M+ | + dedicated infra, multi-user team, pen-test |

---

### Item #7: Workflow builder UI

#### Why this is harder than it looks

Workflows live as JSON in `data/copilot_workflows.json`:

```json
[
  {"name": "Morning brief",
   "prompts": ["What changed overnight? Pull live portfolio, regime..."]
  },
  ...
]
```

A UI to compose these isn't hard. But a useful workflow builder needs
PRIMITIVES beyond "send this prompt." It needs:

- **Triggers:** time-of-day, event ("after rebalance"), threshold ("when
  drawdown > 5%"), webhook
- **Actions:** call HANK with a prompt, run a script, send Slack/email,
  modify a config flag (with safety approval)
- **Conditionals:** "if regime is bear, skip step 2"
- **Variables:** "store this as $today_pnl, use in step 4"
- **State:** workflow runs across days; needs durable execution
  (Temporal / DAG runner)

This becomes a no-code-automation product (Zapier-for-trading). Real but big.

#### Recommended architecture

```
┌─────────────────┐
│  React Flow UI  │  ── visual node-graph editor (canvas)
│  (drag + drop)  │     each node = trigger | action | conditional
└────────┬────────┘
         │ saves to
         ▼
┌─────────────────┐
│  Workflow JSON  │  ── version-controlled per user
│  schema (Zod)   │     each workflow = DAG of steps
└────────┬────────┘
         │ executed by
         ▼
┌─────────────────┐
│  Temporal       │  ── durable workflow runner
│  workflows      │     handles retries, schedules, state
└────────┬────────┘
         │ calls
         ▼
┌─────────────────┐
│  HANK / scripts │
│  / notify / etc │
└─────────────────┘
```

#### MVP scope (skinny version, ~1 week)

Skip Temporal. Skip React Flow. Ship:

1. **Streamlit form-based builder** (not visual graph) where each workflow
   = ordered list of "steps", each step = prompt template + optional Slack/
   email forward
2. **Triggers** = simple cron schedule via launchd (already have this
   pattern with prewarm). User picks "every morning 9am" / "before each
   rebalance" / "after each rebalance"
3. **Actions** = (a) call HANK with prompt, (b) Slack/email the response.
   No conditional logic in MVP.
4. **State** = none. Each workflow run is stateless.

This gets you 70% of the value of full workflow-builder for 5% of the
effort. Ship after multi-tenant lands.

#### Effort

- Streamlit form-based builder: 1 week
- Cron trigger registration: 3 days
- Slack/email forward action: 1 day
- Test + commit: 2 days

**Total: ~2 weeks.**

---

## Roadmap sequence (if you go for it)

```
Month 1: HANK persona polish + per-symbol summaries + email alerts (DONE in v3.64.0)
Month 2: Compliance audit log + self-eval postmortem + Strategy Lab polish (DONE in v3.64.0)
Month 3-5: Multi-tenant + auth + hosted (#4 above)
Month 6: Stripe + billing tiers
Month 7: Workflow builder MVP (#7 above)
Month 8: IBKR adapter (broker #2)
Month 9-10: SOC2 prep + pen-test
Month 11: Soft launch to 20 paid beta users
Month 12: Public launch
```

**Year 1 cost estimate:** ~$250K (2 senior engineers + cloud infra +
SOC2 audit + design contractor). Year 1 revenue at 200 paying users
@ $19-99/mo: ~$100-150K. **You're cash-negative for ~2 years.** Standard
SaaS bootstrap math.

---

## Honest assessment

You COULD ship this as a product. The technical lift is real but bounded.
The harder questions are non-technical:

1. **Regulatory:** are you a Registered Investment Advisor (RIA)? Selling
   "AI trading recommendations" to retail without RIA registration is
   a SEC violation. RIA registration: $5K + ongoing compliance.
2. **Liability:** when a customer's strategy loses money, do they sue?
   Need terms of service that disclaim investment advice. Talk to a
   securities lawyer first.
3. **Differentiation:** Composer, Trade Ideas, Tickeron, Kavout, TipRanks
   already exist. Why is HANK better? Probably: honest backtesting
   discipline + transparent kill-list + open-source-able infrastructure.
   "Trustworthy" is a real wedge in retail-fintech.
4. **AUM economics:** at $19/mo and $50K cap per user, you need ~5K paying
   users to hit $1M ARR. Retail-fintech CAC is brutal (~$200-500 per
   paid user). Need a content / community moat.

**My recommendation if you're serious:** ship the Tier A+B items (DONE),
then pause and validate with 5-10 friends-and-family before investing
in multi-tenant. If they don't get value at v3.64.0, multi-tenant won't
save the product. If they DO, then the architectural lift becomes worth it.

---

*Last updated 2026-05-04 (v3.64.0).*
