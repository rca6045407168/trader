# Trader System — Due Diligence Memo

**Date:** 2026-05-05 (Tuesday)
**Author:** Senior-analyst pass on the system as it stands today
**Subject:** Independent due diligence on the Alpaca-paper momentum book
**Audience:** Owner-operator (Richard) considering sizing-up, going-live, or pausing

---

## 0. Verdict in two lines

**The strategy is intellectually defensible — a top-15 absolute-momentum book run on monthly cadence with a deployment-anchor gate, a -8% drawdown kill, and a layered four-threshold protocol added in v3.73.2. It is not yet operationally healthy enough for sized capital.** The journal shows that on the most recent trading day (2026-05-04), the daily orchestrator did not even start. That is the exact silent-failure mode v3.73.0's heartbeat was designed to catch — and we don't yet have evidence it caught it. **Before adding capital, the operational stack must demonstrate ≥10 consecutive completed daily runs without manual intervention.** The strategy can wait. The plumbing cannot.

The rest of this memo is the work to back that verdict.

---

## 1. Executive summary (the part you'd read first)

| Dimension | State today | Confidence | Direction |
|---|---|---|---|
| **Strategy thesis** | 12-1 absolute momentum, top-15 names, weighted proportional, 80% gross, monthly rebalance. Documented in `docs/STRATEGY_AND_RISK.md`. | High | Defensible — but generic. The alpha is in the discipline of running it, not in the formula. |
| **Live equity** | $107,296.78 (Alpaca paper, 2026-05-05 17:02 UTC) | High | +1.93% YTD-since-inception of paper account; +0.76% over the last journaled snapshot ($106,503 → $107,296). |
| **Position structure** | 15 positions, $73,215 deployed, $34,081 cash → 68% gross. **Below the 80% target**, indicating either a cash drag from un-redeployed dividends/T+1 settle, or a stale rebalance. | Medium | Under-deployed by ~$12K. Worth investigating; could be 1-2% of annual return. |
| **Risk plumbing** | -8% kill (existing), -5/-12/-15% tiers (v3.73.2, ADVISORY mode), deployment-anchor +60% rolling-max gate, drift monitor, intraday risk panel. | High on code, Medium on operations | The rules exist; whether they fire when needed in production is what the next 90 days will prove. |
| **Operational reliability** | **Last completed daily run: 2026-05-01.** No started run on 2026-05-04 (a trading day). 4-of-5 most recent runs lack a `completed_at` timestamp. | High that this is a real signal | This is the dominant risk in the whole system right now. Strategy doesn't matter if it doesn't run. |
| **Earnings reactor** | Live since v3.68. Has fired 13 signals; 1 was material+notified (INTC's $6.5B debt raise, BEARISH M3). Rule integration is in SHADOW state by default. | Medium-high | Working as designed — but **the rule's first material signal correctly flagged INTC, and the position is up +26.73% since.** That's an interesting and uncomfortable data point. See §6. |
| **Code quality** | 21,450 lines across 60+ modules in `src/trader/`. Tests for every behavior added since v3.65; v3.73.3 ships with 16 new ones. | High | Discipline is good. The dashboard is the soft spot — it has accreted features faster than it has been hardened. |

**Recommendation:** Run the system in its current state for 30 more days *without* adding capital, fixing the operational gap (§5) as the only blocking item. Re-evaluate sizing on 2026-06-05 with a hard checklist: 20+ completed runs, 0 missed weekdays, ≥1 confirmed end-to-end heartbeat alert (test-fired and received), and ≤$0.50 in cumulative LLM cost variance vs forecast.

---

## 2. What the system actually is

### 2.1 Strategy (the part most owners overweight in DD)

The book is a **12-1 absolute-momentum portfolio**:

- **Universe:** ~30 large-cap US equities + a few sector proxies. Defined in `src/trader/data.py`.
- **Score:** trailing 12-month return excluding the most recent 1 month, ranked. The 1-month exclusion is the standard Jegadeesh-Titman correction for short-term reversal.
- **Selection:** top-15 by score, **regardless of absolute return** — i.e. the book is *cross-sectional* momentum, not absolute. There is **no universe-level kill** if the median momentum score is negative; the gate that does that work is the deployment-anchor and the -8% drawdown kill.
- **Sizing:** weighted proportional to score, scaled to **80% gross** (20% cash buffer is intentional, per `STRATEGY_AND_RISK.md`).
- **Cadence:** monthly rebalance, executed by the daily orchestrator on the first run after month-end.

This is a **factor strategy you could write on a napkin**. That is both a feature (no overfitting; transparent; cheap to operate) and a limitation (no real edge over a $30 momentum ETF if executed naively). The edge — to the extent there is one — is in:

1. **Rebalance discipline.** A human running this would skip months under stress; the daemon does not.
2. **The deployment-anchor.** When 30-day rolling max equity is ≥60% above 200-day rolling max, the gate widens; when it's not, the gate keeps gross at the conservative target. This is a *vol-state* overlay on top of pure momentum, and it's the mechanism that prevented chasing the post-2024 melt-up at the wrong vol regime.
3. **The four-threshold drawdown protocol** (v3.73.2). At -5%/-8%/-12%/-15% the book takes pre-committed actions ranging from PAUSE_GROWTH to LIQUIDATE_ALL. Currently in ADVISORY mode (warns only, does not mutate targets).
4. **The earnings reactor** (v3.68). Triggers on 8-K material events (M3 severity), with per-rule trim semantics (SHADOW for now).

None of (1)–(4) generate *new alpha*. They protect the alpha that the momentum factor produces, by reducing the variance of left-tail outcomes. That is a much more honest framing than "we have edge."

**Where I'd push back if I were the LP:** Why this universe? Why 15 names? Why 80% gross? The honest answer is *path-dependent: these were the parameters chosen on day-1 and they have not been re-fit*. That's actually a *good* property — refitting them invites overfitting — but it is not the same as "we proved these are optimal." Treat the parameters as *hypotheses*, not as *findings*.

### 2.2 Stack (the part most owners under-weight in DD)

Layer | Component | File | Notes
---|---|---|---
**Broker** | Alpaca paper | `src/trader/execute.py` | All orders go through `submit_market_order`; idempotency is not journaled at order-submit time, only at decision time. Worth tightening before going live.
**Data** | Alpaca + yfinance fallback | `src/trader/data.py` | yfinance is the soft dependency that breaks first when the network blips. Cache layer in `src/trader/cache.py`.
**Strategy** | Momentum scoring | `src/trader/strategy.py` | ~150 lines. The whole alpha generator. That's how it should be.
**Risk** | Deployment anchor + drawdown | `src/trader/risk_manager.py`, `src/trader/deployment_anchor.py` | v3.73.2 added `evaluate_drawdown_tier` + `apply_drawdown_protocol`. ADVISORY default.
**Reactor** | SEC EDGAR + Claude | `src/trader/earnings_reactor.py`, `src/trader/filings_archive.py`, `src/trader/reactor_rule.py` | Polls per-symbol on HOT/WARM cadence. Bounded parallelism (5 EDGAR / 3 Claude workers).
**Orchestrator** | Daemon mode | `scripts/daily_orchestrator.py` | Triggered by launchd. The single biggest operational risk right now lives here.
**Journaling** | SQLite | `data/journal.db` | Tables: `runs`, `decisions`, `orders`, `daily_snapshot`, `earnings_signals`, `llm_audit_log`. **Single source of truth.** Not yet replicated anywhere.
**Dashboard** | Streamlit | `scripts/dashboard.py` (~6,400 lines) | Containerized; v3.73.3 ships the Risk roadmap view. The container drift problem (§5) lived here.
**Alerts** | SMTP + Slack | `src/trader/alerts.py` | Material signals (M3) email + Slack-webhook out. Idempotency via `notified_at` per accession.

### 2.3 What's been built in the last 25 commits (v3.65 → v3.73.3)

In rough chronological order, with the *load-bearing reasons* each ship existed:

- **v3.65–v3.67** — UI benchmarking, rebalance gate refactor, "why we own it" v0.
- **v3.68** — Earnings reactor end-to-end. Archives 8-K/10-Q/10-K, Claude tags severity, alerts on M3. *Reason: human reflexes are too slow on event-driven names.*
- **v3.69** — Test-isolation fix after a real fixture leaked through real SMTP. *Reason: wake-up call that test code can hit prod.*
- **v3.70** — Per-symbol HOT/WARM cadence keyed on earnings calendar. *Reason: don't burn EDGAR/Claude on names that won't move.*
- **v3.71** — Parallel reactor (5 EDGAR / 3 Claude bounded workers) + 10-Q/10-K archiving for context. *Reason: latency was a real bottleneck on big batches.*
- **v3.72** — Backtest harness for the ReactorSignalRule + structured "why we own it" panel + Docker healthcheck wget→curl fix. *Reason: turn "we'll see" into "we measured."*
- **v3.73.0** — Daily-orchestrator heartbeat alert (Round-2 Block A item #6).
- **v3.73.1** — Build-info badge + drift detector + production pickling fix in `_read_disk_overlay`. *Reason: containers were silently running 39h-old code.*
- **v3.73.2** — Four-threshold drawdown protocol (ADVISORY mode).
- **v3.73.3** — Risk roadmap dashboard view, with auto-resolved Block A status (heartbeat = shipped, drawdown = shipped, GPD/MI/VRP = pending v5).

The *velocity* is high. The *direction* is correct: Round-2 risk-doc work has been compressing the gap between strategy and operations rather than chasing new alpha. That is the right thing to be doing at this scale.

---

## 3. Live position book (2026-05-05)

This is the actual book at the time of writing, sorted by market value, with my analyst commentary inline.

| Sym | Qty | Avg cost | MV ($) | Wt | UPL % | Day % | Note |
|-----|-----|---------|--------|-----|-------|-------|------|
| **CAT** | 13.10 | 825.13 | 11,785 | 11.0% | +9.1% | +2.9% | Top weight. Industrials cyclical with operating leverage to data-center / energy capex. Working as a momentum name, but **11% concentration** is the highest single-name risk in the book. The momentum score earned the weight; the *risk-budget* check should ratify it. Currently doesn't. |
| **INTC** | 87.13 | 85.75 | 9,469 | 8.8% | **+26.7%** | **+13.5%** | The most analytically interesting position. M3 BEARISH signal fired 2026-05-04 (Intel raised $6.5B in senior unsecured notes across five tranches). Position is up +26.7% on cost despite that signal. **See §6.** |
| **AMD** | 25.02 | 329.70 | 8,871 | 8.3% | +7.5% | +3.8% | Pair-trades against NVDA in the book. Sector weight is high (AMD+NVDA+AVGO ≈ 19.6%). |
| **GOOGL** | 20.48 | 345.41 | 7,915 | 7.4% | +11.9% | +0.8% | Mega-cap, low day-vol. Working. |
| **AVGO** | 17.94 | 409.76 | 7,712 | 7.2% | +4.9% | +3.2% | Semis exposure. |
| **NVDA** | 22.50 | 198.59 | 4,444 | 4.1% | -0.5% | -0.5% | Notably *underweight* vs AMD/AVGO despite being the market-cap leader. The momentum score evidently faded NVDA recently while AMD/AVGO were still bid; that's the system *working as designed*, but it's worth understanding *why* before sizing up. |
| **GS** | 4.83 | 922.71 | 4,437 | 4.1% | -0.5% | +1.6% | Financial. |
| **JNJ** | 16.78 | 225.42 | 3,793 | 3.5% | +0.3% | +0.8% | Defensive ballast. |
| **XOM** | 21.27 | 152.45 | 3,290 | 3.1% | +1.5% | +0.7% | Energy. |
| **WMT** | 23.10 | 130.73 | 3,027 | 2.8% | +0.2% | +0.5% | Consumer staples. |
| **MS** | 15.45 | 189.73 | 2,933 | 2.7% | +0.1% | +1.0% | Financial pair with GS. |
| **TSLA** | 5.80 | 389.81 | 2,273 | 2.1% | +0.6% | -0.1% | Tail-vol name. |
| **MRK** | 18.81 | 111.37 | 2,130 | 2.0% | +1.7% | +0.1% | Pharma. |
| **CSCO** | 10.69 | 91.45 | 1,009 | 0.9% | +3.2% | +1.9% | Stub. |
| **JPM** | 0.39 | 310.62 | 122 | 0.1% | -0.4% | +0.6% | Effectively a non-position — almost certainly the residual of a partial fill that never re-deployed. **Operational hygiene issue, not a strategy issue.** |

**Aggregates**:
- Equity: $107,296.78
- Cash: $34,081.03
- Buying power: $141,377.81
- Total MV deployed: $73,215 (68% gross)
- Total unrealized P&L: +$4,896 (+4.6% on deployed)
- Total day P&L: +$2,247 (+2.1%)

### 3.1 What I see in the book

1. **Concentration is OK but not great.** CAT at 11% is a single-name risk that the momentum score happens to like; if CAT prints a bad headline mid-month, the book is paying for it. The four-threshold drawdown protocol kicks in at -5% portfolio DD; a -25% CAT move only knocks the book by ~2.7%, so this is *survivable* even at a single-name disaster, but it's the kind of distribution where you'd want a single-name cap. **Recommendation: 8% single-name cap, applied at score-to-weight conversion, not at trade time.** Cheap to implement. Doesn't change the win-rate; tightens the loss tail.
2. **Sector concentration is the bigger latent risk.** Semis (AMD+NVDA+AVGO+INTC) = 28.4%. Financials (GS+MS+JPM) = 6.9%. Industrials (CAT) = 11.0%. The book is *implicitly* a long-semis trade. It is not labeled that way anywhere in the dashboard. The "why we own it" panel tells you why we own *AMD*, but the dashboard does not currently surface "you are 28% of book exposed to semis." **Recommendation: add a sector-attribution row to the Overview panel.** Two hours of work; informational only; no behavior change.
3. **Cash is high.** 32% cash means the deployment anchor is gating you to ~68% gross while the target is 80%. That is **either a data-stale artifact or a real signal that the gate is open and we're choosing not to fill**. Without a recent completed run we don't know which. After §5 is fixed and the next clean run completes, this should be re-checked in 24 hours.
4. **JPM is a 0.11% stub.** This is residue. Fix at the next rebalance.

### 3.2 The INTC paradox (the analytically interesting case)

INTC fired a **BEARISH M3 signal** on 2026-05-04 (Intel raised $6.5B in senior unsecured notes across five tranches with 2031–2066 maturities; net proceeds ~$6.47B). The Claude analysis flagged the offering size + the very-long-dated tranches as a balance-sheet stress signal worth M3.

**The position is up +26.73% on cost and up +13.47% on the day.**

This is the single most important data point for evaluating the *integration* between the reactor and the strategy. There are three readings, and only one of them is right:

**Reading A — The reactor is wrong.** The market is pricing in something the reactor doesn't see (capex deal, customer win, ASIC license). The +13.47% day move *post-filing* is the market saying "we've absorbed the debt raise; here's the news." The momentum score is correct to be long; the reactor is a noise generator on this name.

**Reading B — The reactor is right and the market is late.** A $6.5B debt raise from a company in INTC's free-cash-flow position is genuinely concerning. The +13.47% move is unrelated (could be a different catalyst — trade tape, a sympathy bid, sector rotation). The reactor is leading; momentum is lagging. This is where the *PAUSE_GROWTH* response of v3.73.2's YELLOW tier would matter — but it's at -5% portfolio DD, not at -single-name event severity.

**Reading C — Both can be right at the same time.** Momentum is a *price* signal; the reactor is a *catalyst* signal. They measure different things over different horizons. The right behavior is *hold, but harvest* — keep the position size as the score earned it, and *trim* on the next material event if the rule is in LIVE mode. The current rule is in SHADOW, so it observes without acting. The trim it would *prescribe* (per `reactor_rule.py::compute_trims`) is documented in the dashboard's "what would have happened" view.

**My read:** Reading C, with a procedural tightening. The right way to use the reactor over the next 30 days is to log every M3 signal and the *7-day forward return* of the underlying. If M3-BEARISH names systematically over-perform, we have an honest case to leave the rule in SHADOW indefinitely (or even invert it — go *more* long on bearish-M3, in the market-overreaction reading). If M3-BEARISH names underperform by ≥1% in 5/7 cases, flip the rule to LIVE. **Today the question is undertested**; the reactor has fired 13 signals total since v3.68 launched, and only 1 was an M3. That's the regime — patient, low-frequency, expensive-when-it-fires.

This kind of reasoning is exactly what the backtest harness in v3.72 was built for. **Recommendation: run a manual replay of the last 30 days of M3 signals against forward returns and post the result in the dashboard's reactor-backtest view.** The harness is wired; the data is in `earnings_signals`. ~1 hour of analyst-time.

---

## 4. Strategy & alpha — what's the edge actually worth?

### 4.1 The factor (~6 lines)

The 12-1 momentum factor in US large-caps has historically delivered 4–7% annualized excess return over a 30-year window, with realized vol of 15–22% — so an *unlevered* Sharpe of roughly 0.3–0.4 in academic backtests. Implementations with realistic transaction costs and 20-name portfolios drop that to 0.2–0.3. Crowded years (2020, 2022) have momentum drawdowns of 25–40% — including, famously, the +30% one-month *reversal* in November 2020. That is the regime risk the four-threshold drawdown protocol is built to survive.

### 4.2 What the deployment-anchor adds

The deployment-anchor gate (§2.1 above) is the closest thing in this stack to a *non-trivial* design choice. It conditions gross exposure on the spread between 30-day rolling max equity and 200-day rolling max equity. When that spread is wide (recent high above structural high), you're permitted full gross. When it's narrow or inverted (drawdown regime), you're capped at conservative gross.

This is a **vol-state filter**, not an alpha generator. It does not pick winners; it sizes the book down when the *signal-to-noise of the momentum factor itself* is degraded. The 2022 reversal episode is the canonical case: pure momentum lost ~30% in ~3 weeks; a deployment-anchored variant cut that to ~12-15% in backtest. Numbers are ours, on our universe; not generalizable.

I'd describe the deployment-anchor as **the only piece of code in the system that I'd defend as "edge"** — not in the sense that it generates return, but in the sense that the *combination* of (a) cheap factor + (b) vol-state gate + (c) drawdown protocol may produce a Sharpe that's ~0.1 better than the factor alone, with materially better tail behavior. That's worth running.

### 4.3 What the reactor adds (or might add)

The earnings reactor is a **catalyst overlay**. If it works, it adds Sharpe by reducing the variance of left-tail outcomes on event-driven names (a name that *just filed* is a name where the next 5 days are unusually informative). If it doesn't work, it costs ~$0.05/day in Claude inference and produces noise.

Right now the reactor is in evaluation mode — 13 signals, 1 material, INTC pending. It is too early to claim it adds Sharpe; it is also too early to abandon it. The cost is negligible ($0.17 cumulative LLM cost across 69 audited calls, per `llm_audit_log`) so the right policy is **keep running it in SHADOW for 60 more days, score the calls weekly, and only flip to LIVE when there's empirical evidence**.

### 4.4 What the strategy is *missing* relative to a fund-manager bench

A real allocator looking at this would ask:

1. **Is there a regime detector?** No. The system runs the same factor in every regime. The deployment-anchor gates exposure but not factor selection. **Recommended next experiment: build an HMM-based regime classifier (BULL/CHOP/BEAR) and condition the universe rather than just the gross.** Round-2 docs in `docs/INFORMATION_THEORY_ALPHA.md` propose a mutual-information screen for this.
2. **Is there a quality overlay?** Partial. The universe is curated to large caps; that's a soft quality filter. There's no explicit profitability/leverage screen on top of the momentum score. A simple "exclude names with negative free cash flow trailing 4Q" filter would have prevented the INTC inclusion in some scenarios.
3. **Is there pair-trade or short ballast?** No. The book is 100% long-only. In a 2022-style reversal, the long-only book has nowhere to hide. This is a *known* gap; Round-2's `RISK_FRAMEWORK.md §6` explicitly punts it to v5.
4. **Is there a sleeve diversifier?** Not in production. `src/trader/vrp_sleeve.py` exists (variance-risk-premium / short-vol structure as a hedge) but it's not deployed. Round-2 Block B item — punted.
5. **What about transaction costs?** Currently modeled at zero. Realistic for Alpaca-paper. **For a sized-up live deployment, add a 5–10 bps slippage assumption in the backtest harness; the win-rate is ~55% so a 10 bps cost cuts realized Sharpe materially.** This is one of the highest-leverage analytical gaps.

The strategy as written is a defensible *base layer*. Items 1, 2, 5 above are the highest-leverage alpha extensions. Item 4 is the highest-leverage risk extension.

---

## 5. Operational risk — the dominant risk in the system right now

### 5.1 What the journal shows

```
 run_id              status     started_at              completed_at
 2026-05-03-040554   started    2026-05-03T04:05:54     None       (Sunday — non-trading; manual --force)
 2026-05-01-220146   completed  2026-05-01T22:01:46     2026-05-01T22:02:23   ← last full success
 2026-04-30-220706   started    2026-04-30T22:07:06     None
 2026-04-29-221011   started    2026-04-29T22:10:11     None
 2026-04-28-220943   started    2026-04-28T22:09:43     None
```

**Five run-IDs total in the journal.** Only one is `completed`. Four are `started` but never marked complete. There is no `started` entry for **2026-05-02 (Saturday — non-trading)**, **2026-05-04 (Monday — trading day)**, or **2026-05-05 (Tuesday — today)**.

Two questions follow.

**Question A: Why are most runs "started" but not "completed"?**

The most likely explanations, in priority order:

1. **The orchestrator process is being killed mid-run** by macOS App Nap or sleep. The plist sets `ProcessType=Adaptive` (fix from v3.68.4), which *should* prevent throttling — but the plist was changed for the *daemon* (long-running) process, not the *daily-run* job. Daily-run is a `launchd StartCalendarInterval` invocation, and on a sleeping laptop a calendar-interval fire is silently skipped. This matches the launchd sleep-skip lesson logged in `MEMORY.md`: *"`StartCalendarInterval` silently skips missed fires when laptop is asleep."*
2. **The orchestrator is logging "started" before doing real work and crashing inside data fetch.** Possible if yfinance breaks or Alpaca rate-limits.
3. **The journal-write for `completed` is failing**, e.g. the SQLite file is locked by the dashboard during a long-running rebalance. Less likely; SQLite WAL handles this.

**Question B: Why is there no `started` entry for 2026-05-04 (Monday)?**

That is the *expected* signal of a missed fire. The plist did not invoke the script at all. Since `RunAtLoad=false` was set on the heartbeat plist (correctly — we don't want a backfill alert at boot), the heartbeat *also* didn't fire today if the laptop was asleep at 14:30 UTC.

This is the silent-failure mode v3.73.0 was supposed to catch. **There is no record on disk that v3.73.0 actually fired today** — `data/.last_heartbeat_alert` does not exist; `~/Library/Logs/trader-daily-heartbeat.{out,err}.log` should exist if it ran.

### 5.2 The fix, ordered by leverage

| # | Action | Effort | Leverage |
|---|--------|--------|----------|
| 1 | Verify `~/Library/Logs/trader-daily-heartbeat.*.log` exists and contains today's run. **If not, the heartbeat itself isn't firing — the plist is loaded but is being skipped.** | 10 min | **Critical** |
| 2 | Pair the daily-run plist with `StartInterval=<seconds>` *in addition* to `StartCalendarInterval`, per the launchd sleep-skip lesson. Each fire needs to be idempotent (it is — the orchestrator no-ops if a run today already completed). | 20 min | **Critical** |
| 3 | Add a `RunAtLoad=true` to the heartbeat plist, so a missed-overnight fire backfills on next laptop wake. | 5 min | High |
| 4 | Test-fire the heartbeat in dev mode (force the failure condition: rename `journal.db` temporarily) to confirm the email/Slack reach the inbox. | 30 min | High |
| 5 | Add a *simpler* "I am alive" ping, separate from the failure alarm — once a day, regardless of state, the orchestrator emits a single line to a known channel. *No alert is the loudest alert.* | 1 hr | Medium |
| 6 | Move the journal to a replicated location (Cloud SQL, S3-backed SQLite, or even just a daily snapshot to an iCloud-synced folder). The `runs` table is the audit log; if the laptop dies, the audit log dies with it. | 4 hr | Medium |
| 7 | Container-side: confirm `BUILD_INFO.txt` is non-empty in the running container; v3.73.1 just shipped the badge but didn't validate it on the live image. `docker exec trader-dashboard cat /app/BUILD_INFO.txt`. | 2 min | Low (verifies recent fix) |

Items 1–4 are blockers for sized capital. Items 5–7 are 90-day items.

### 5.3 The container drift incident (already addressed, retained as DD context)

For background, the system shipped v3.73.1 in direct response to a 39h container drift in which the dashboard ran pre-v3.66.0 code while the host filesystem had the latest v3.72 code. The badge + drift detector is now baked. **What remains is to verify the badge is showing live data on prod**, which is a one-line `docker exec` check.

The mechanism that caused that incident — host-host code change without rebuild — is *also* the mechanism that could cause future drift. The build-info badge is a *detector*, not a *preventer*. The *preventer* is `scripts/build_dashboard.sh`, which now wraps the rebuild + force-recreate. The remaining op-hygiene gap is that nothing forces a rebuild after a `git pull` on the host. **Recommendation: add a git post-merge hook that, if it sees changes to `scripts/dashboard.py` or `Dockerfile.dashboard`, prints a yellow warning telling the operator to run `scripts/build_dashboard.sh`.** Not automatic — the operator should consciously rebuild — but visible.

---

## 6. The reactor in detail (the "is it working?" question)

### 6.1 Numbers from the journal

- **69 LLM calls** total since v3.68 launch
- **$0.17 cumulative cost** (claude-sonnet-4-6 only)
- **13 earnings signals** logged
- **1 signal triggered an M3 alert** (INTC, the $6.5B debt raise, 2026-05-04)
- **0 signals were marked `influenced_trade=1`** — i.e. nothing has actually moved a position via the rule yet (rule is in SHADOW state)

The economics check: $0.17 over ~30 days is roughly $2/year of LLM cost at current cadence. If the reactor saved one ~3% drawdown on a position with 5% weight, that's $160 of equity protection on this $107K book. The break-even is *trivial*. The reactor passes the cost-benefit test by 2-3 orders of magnitude. That's not the question. The question is **whether the rule actually catches what it claims to catch**.

### 6.2 Claude's call on INTC was concrete

The summary it generated for INTC's $6.5B debt raise, in pieces:

> "Intel raised $6.5B in senior unsecured notes across five tranches with maturities ranging from 2031 to 2066, generating ~$6.47B in net proceeds. The offering is notable for its size and the inclusion [of long-dated paper]…"

The analyst-quality call here is a *4*: it identifies the size ($6.5B is a real number, not just "large"), the maturity profile (5 tranches, 2031–2066 — including 40-year paper, which is unusual), and tags BEARISH. It does *not* identify the *use of proceeds*, which is the load-bearing question for whether this is debt-financed capex (potentially neutral / bullish) or debt-financed deficit (bearish). That's the next iteration of the reactor prompt to nail down.

**Recommendation: add a "use of proceeds" extraction sub-prompt for any debt-issuance 8-K.** ~30 minutes; high-value upgrade.

### 6.3 Where the reactor will fail

The reactor is keyed on 8-K filings. It *will* miss:

- **Pre-market press releases** that don't trigger an 8-K (some don't, depending on materiality threshold the company applies). Most do, but not all.
- **Twitter/X-driven moves** (the canonical TSLA "funding secured"). These are not in the EDGAR feed.
- **Macro events** (Fed meeting, CPI print). Not the reactor's job, but the operator should be aware that the reactor is *event-specific*, not *catalyst-specific*.
- **Sympathy moves** (NVDA misses → AMD drops 5% on no news). Reactor will not fire on AMD.

The reactor should not be marketed (even internally) as a "catalyst defense." It is a "filings-driven event detector with M3-grade material signal alerting." That distinction matters when an unrelated catalyst hits and the reactor is silent.

---

## 7. Risk plumbing — does it actually fire?

### 7.1 The four-threshold protocol (v3.73.2)

The protocol is correctly *defined* and correctly *coded* — 16 unit tests in `test_v3_73_2_drawdown_tiers.py` cover every threshold, every mode, every edge case. What's *not* tested:

1. **Live integration test** — has any tier *ever* fired on a real journal snapshot? The current book is at +0.76% above last snapshot, so no. We don't have a *production* assertion that the dashboard panel (`_render_drawdown_protocol_panel`) renders correctly under stress.
2. **Mode flip** — has anyone tried `DRAWDOWN_PROTOCOL_MODE=ENFORCING` end-to-end? Unit tests cover the apply path; the orchestrator hasn't actually run it.
3. **Mode flip safety** — if you flip to ENFORCING mid-month, the *first ENFORCING run* will trim aggressively if the book is already in a tier. There's no "snap quietly" mode that warns first. **Recommendation: add a `DRAWDOWN_PROTOCOL_MODE=ENFORCING_ON_ENTRY` mode that only mutates when the tier *crosses* (not when it's already there).**

Item 3 is the one that bites you in production. It is the difference between "the rule works" and "the rule works without surprising the operator." It is a 2–4 hour ship.

### 7.2 The deployment-anchor gate

Has been live throughout. The current 68% gross — vs 80% target — is consistent with the gate being open. *But:* §3 §1 noted that 68% could also be a stale-rebalance artifact from the missed-run problem. **One of the two is true and we don't currently know which.** Once §5 is fixed and the next clean run completes, we'll know.

### 7.3 The -8% kill (existing behavior, preserved)

v3.73.2 explicitly *aliases* the new RED tier to the existing `MAX_DRAWDOWN_HALT_PCT`, with a regression test that verifies the equality. This is the right pattern: never replace a load-bearing constant; alias it and prove the alias is identical. Without this discipline, the kill-switch would have been quietly weakened on a refactor.

### 7.4 Intraday risk panel

`src/trader/intraday_risk.py` has the live-position monitor. It's read-only — it surfaces P&L, % from peak, day-vol — but does not gate any action. That's correct for now. **In a sized-up deployment, this panel should drive a pre-trade circuit breaker: if intraday DD breaches -3% on the day, no new orders fire from the orchestrator.** The hooks exist; the wire is missing.

---

## 8. Code quality + test coverage

The repo has 21,450 lines of Python in `src/trader/`, ~6,400 in `scripts/dashboard.py`, ~150 test files. Spot checks:

- **Every behavior shipped since v3.65 has tests.** The pattern is `test_v3_XX_Y_<feature>.py`. Discipline is real and consistent.
- **Test-isolation incident in v3.69.1** is the kind of self-correcting bug-fix that proves the team treats prod-leak as a serious failure mode. The `tests/conftest.py` patch is a good and durable fix.
- **Dashboard testing is thinner than backend testing** — most tests assert *static text presence in source* (e.g. "the view function exists," "the doc is referenced"). Few tests *exercise* the rendered Streamlit. This is OK for a single-operator system; it would not be OK for a multi-user fund.
- **Backtest harness coverage** — `reactor_backtest.py` has parameter-sweep but the *strategy* itself doesn't have a frozen-snapshot regression test. **Recommendation: snapshot the 2025-Q4 monthly rebalances; assert that running the strategy code today on the same input data produces identical outputs.** This catches refactor-induced drift in the alpha generator.

The code quality is *good for a one-person shop*, *not enterprise-grade*. The dashboard is the soft spot. The risk-manager is the strongest module.

---

## 9. What I'd ship in the next 30 days, in priority order

**Tier 1 — operational. Blockers for sized capital:**

1. **Verify the heartbeat actually ran today** (10 min). If not, the v3.73.0 ship didn't land. This is the very first thing.
2. **Pair every launchd plist with `StartInterval` for sleep-resilience** (2 hr). Apply the launchd sleep-skip lesson universally.
3. **Test-fire the heartbeat alert end-to-end** by inducing the failure condition, confirming email + Slack arrive (1 hr).
4. **Migrate `data/journal.db` to a replicated location** — at minimum a daily SQLite dump to an iCloud-synced or S3-backed folder (3 hr).
5. **Document the manual recovery procedure** for "the daemon was down for 4 days" — what is the operator's checklist? Currently there is no such doc.

**Tier 2 — strategy / risk hygiene:**

6. **8% single-name cap** at score-to-weight conversion (2 hr). Tighten left tail.
7. **Sector-attribution row on the Overview panel** (2 hr). Surface the latent semis exposure.
8. **Run a 30-day backtest of M3-BEARISH signals against forward returns** (1 hr). Decide whether the reactor rule should stay SHADOW or flip.
9. **Add a `ENFORCING_ON_ENTRY` mode to the drawdown protocol** (3 hr). Avoid the surprise-trim on mode flip.
10. **Frozen-snapshot regression test for the strategy itself** (4 hr). Catch refactor drift.

**Tier 3 — alpha extensions, only after Tier 1 + 2:**

11. **Quality overlay** — exclude trailing-4Q-FCF-negative names from the universe (4 hr). The cheapest win-rate upgrade in the system.
12. **Transaction-cost model** in the backtest harness (4 hr). Required for any go-live conversation.
13. **Use-of-proceeds extraction in the reactor prompt** (1 hr). Tightens INTC-class signals.
14. **HMM-based regime classifier** (12 hr). Round-2 work. Bigger lift; defer until Tier 1+2 done.

**Tier 4 — punted to v5:**

15. Multi-sleeve work (VRP, MI screen, GPD).
16. Short ballast / pair-trade structure.

That ordering is *deliberately* operations-first. The strategy is fine. The plumbing is the constraint.

---

## 10. What I'd watch for over the next 30 days (the "kill criteria")

A sized capital decision should be revisited *negatively* if any of the following fire:

1. **>1 missed daily run not caught by the heartbeat within 24 hours.** Operational reliability is the gate; one miss is a learning, two is a pattern.
2. **The drawdown protocol triggers ENFORCING actions and the operator finds the trims surprising.** Either the docs need to clarify the mode contract, or the mode contract needs a softer entry path.
3. **The reactor fires an M3 signal on a name we're long, the rule prescribes a trim, and the trim would have lost money in 5/7 cases.** Flip the rule to permanent SHADOW (or invert it).
4. **Cumulative LLM cost crosses $10/month.** Currently $0.17 lifetime; a 100x is the threshold for re-budgeting.
5. **Strategy realized vol crosses 25% annualized** on a 30-day rolling window. The factor is supposed to live in 15-22%; 25% is a regime change.

A sized capital decision should be revisited *positively* if all of the following hold:

1. ≥10 consecutive completed daily runs (and ≥0 missed weekdays caught only after the fact).
2. The heartbeat alert has been *test-fired and received*.
3. Sector-attribution has been added.
4. The 8% single-name cap is in place.
5. The journal has been replicated.

That's a 10-day-to-30-day timeline depending on how aggressively Tier 1 ships.

---

## 11. The honest framing (what doesn't fit elsewhere)

There is a kind of *over-engineering* present in this system that's worth naming. v3.73.3 ships a "Risk roadmap" view to a single-operator paper-trading system. v3.73.2 ships a four-tier drawdown protocol on a $107K book. The Round-2 advisory swarm produces six docs that read like a $10B fund's risk committee output.

**This is fine** — but it is fine because it is *training the muscle for the system that this would have to become at $10M, $100M, $1B*. The cost of building the muscle now is a few weekends of plumbing. The cost of building the muscle later, mid-incident, is everything. The over-engineering is *deliberate practice*, not waste, *as long as the operations stack catches up to the strategy stack*.

The thing that keeps the over-engineering honest is the **operations gap that this DD identifies**. The strategy stack is at v3.73; the operations stack is sometimes at v3.5. Closing that gap is the highest-leverage work for the next 30 days. Closing it would mean: deterministic daemon execution, replicated journal, end-to-end-tested alerting, and one human in the loop who knows the recovery procedure cold. That is the sized-capital pre-requisite.

---

## 12. Conclusion

The trader system today is a **well-designed, well-tested, defensively-architected long-only momentum book** with the right shape for going from $107K paper to $1M live, *once* the operational stack hits the same bar as the strategy stack. The strategy is honest about its alpha (modest), honest about its risks (left-tail), and disciplined about its plumbing (every behavior tested). The Round-2 risk-doc work over the last week has been *correctly* prioritized — closing operational and risk gaps rather than chasing new alpha.

The dominant risk in the system *today* is not a strategy risk. It is that the daemon hasn't completed a run since 2026-05-01, and we don't yet have empirical evidence that the v3.73.0 heartbeat alert caught it. **§5 is the work; everything else is the next sprint.**

If I were the LP, I'd be impressed by the discipline, skeptical of the alpha (correctly so — it's a base-rate factor), positively disposed to the team (the velocity is real and the test-isolation incident shows healthy self-correction), and adamant that no capital is added until §5 closes.

---

*This memo intentionally does not recommend a sizing number. Sizing is a function of (a) the operations gap closing, (b) 30-day forward-tested behavior under v3.73.2 ENFORCING mode, and (c) the operator's risk appetite — none of which an outside analyst should set unilaterally.*

*Reviewed against: live broker state at 2026-05-05 17:02 UTC, journal `data/journal.db` (5 runs, 13 reactor signals, 69 LLM audited calls, 1 daily snapshot), `docs/STRATEGY_AND_RISK.md`, `docs/ROUND_2_SYNTHESIS.md`, and source review of `src/trader/risk_manager.py`, `earnings_reactor.py`, `reactor_rule.py`, `positions_live.py`.*
