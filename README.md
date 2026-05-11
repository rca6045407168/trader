# trader

Personal multi-strategy quant platform on Alpaca paper, **active under v6.0.x** as of 2026-05-10. v6 is a structural-edge ship — where v3.x–v5 leaned on momentum alpha, v6 stacks **8 independent edges** with most of them structural rather than alpha-decaying.

## Operating mode (today)

**Two execution venues, intentionally separated:**

- **Alpaca paper** (automated): the validation substrate. Auto-router runs daily, submits orders to Alpaca paper, journal reconciles to broker. Proves the logic.
- **Public.com** (manual, real money): no public API — operator manually executes signals from the weekly digest. The trader is a **signal generator** here, not an order submitter.

Workflow: Friday `weekly_digest.py` → Monday morning execute on Public.com → Monday evening `import_public_positions.py` reconciles → repeat.

**Edge stack (v6.0.x):**
- **Two-book architecture**: Book A (TLH direct-index core, 70% by default, env-gated) + Book B (auto-router alpha sleeve, 30%).
- **32 strategies in the eval pool**; auto-router picks the rolling-IR winner under an eligibility filter (≥6 months evidence, β ≤ 1.20, DD ≥ −25%, hysteresis to prevent churn).
- **4 always-on overlays** (vol-target, HIFO close-lot, drawdown-aware, calendar-effect).
- **3 opt-in alpha sources** (insider EDGAR Form-4, PEAD, quality tilt) — shadow-tracked by default; promote when 6 months of evidence accrues.
- **138-name expanded universe** (11 sectors including Utilities + Real Estate) opt-in via `UNIVERSE_SIZE=expanded`. Recommended for Public.com workflow: stick with 50 names to reduce manual-reconciliation friction.
- Market-open gate enforced — no weekend/holiday order submissions unless `ALLOW_WEEKEND_ORDERS=1`.

## Expected after-tax uplift over SPY

Composition of all v6 edges, full activation in a real taxable account:

| Source | Expected |
|---|---|
| TLH tax shelter (HIFO + quality) | +1.5–2.0 %/yr |
| Quality factor (Novy-Marx) | +0.3–0.7 %/yr |
| Insider buying (SEC EDGAR 30d) | +2.0–3.0 %/yr |
| PEAD (post-earnings drift) | +1.0–2.0 %/yr |
| Calendar-effect overlay | +0.3–0.5 %/yr |
| Universe expansion (more TLH scope) | +0.3–0.6 %/yr |
| Stock lending + cash interest | +0.15–0.8 %/yr |
| Vol-targeted alpha sleeve | Sharpe-only (path) |
| **TOTAL OVER SPY (after-tax)** | **+5.0–9.0 %/yr** |

Pessimistic (factor decay / regime change) shrinks this to +2–4 %/yr. Every component is env-gated and reversible.

## Canonical references

- **`ARCHITECTURE.md`** — system architecture, every layer's mechanism, the full disposition record (v3.x → v6.0.x)
- **`V5_DISPOSITION.md`** — the multi-strategy frame + capital ladder (still the operating spec; v6 is an extension, not a replacement)
- **`RUNBOOK_MAX_RETURN.md`** — operator playbook: Alpaca app toggles, env activation order, tax-time checklist, expected uplift breakdown

## Production daemons

| Daemon | Schedule | Purpose |
|---|---|---|
| `com.trader.daily-run` | 1:10 PM PT weekdays | Orchestrator: reconcile, build targets, submit orders (market-open gated) |
| `com.trader.shadow-eval` | 1:30 PM PT weekdays | Records picks for all 32 strategies → feeds auto-router eligibility filter |
| `com.trader.daily-heartbeat` | 1:30 PM PT weekdays | Silent-cron-failure detector |
| `com.trader.earnings-reactor` | 4× daily | SEC 8-K poller → populates earnings_signals (feeds PEAD strategy) |
| `com.trader.journal-replicate` | Nightly | iCloud backup of `data/journal.db` |

## Version history (short)

- **v3.x** (six months, ~28 versioned releases): build years — single-strategy momentum with risk gates
- **v4.0.0** (2026-05-07): freeze + stop-rule disposition after harsh review surfaced the IR-falsification
- **v4.1.0** (~3 hrs later): path-C sunset — daemons unloaded
- **v5.0.0** (same day reversal): multi-strategy auto-router frame, stop-rule explicitly retired
- **v6.0.x** (2026-05-10): structural-edge stack — TLH + 4 overlays + 4 new alpha sources + universe expansion

`ARCHITECTURE.md` §3 + §11 have the full record including the reasoning chain at each transition.
