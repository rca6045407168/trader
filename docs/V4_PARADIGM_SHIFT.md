# v4 paradigm shift — multi-strategy capital allocator

**Status:** research spike (2026-05-03). Not started; this doc is the design.

## What v3.x is

The entire v3.x line is **incremental layering on a single rule-based
momentum core**. Same fundamental rule across 50+ versions:

- Rank S&P 500 (PIT) by 12‑1 momentum
- Hold top‑15, weight by score, 80% gross, monthly rebalance
- Plus defensive overlays: regime gating, intraday risk, freeze states

Everything else — dashboard, reports, sleeve health, adversarial reviewer,
GCP plan — is *better plumbing around the same engine*. That's healthy and
necessary, but it's not a paradigm shift.

## What v4 is

**Stop being “momentum top‑15 with overlays.” Become “a portfolio of
4–6 uncorrelated strategies with dynamic capital allocation between them
based on regime + rolling Sharpe + factor risk budget.”**

This is the AQR / Bridgewater / Citadel multi‑strat pattern, scaled down
to a personal Roth IRA. The math changes: portfolio Sharpe stops depending
on any single factor's persistence.

### Target architecture

```
     equity
        │
        │  meta_allocator (v3.49.0 scaffold; wired LIVE in v4)
        │
   ┌────┼───────────────────────────────────────┐
   │    │                                                  │
  35% momentum                                              5% merger‑arb
        │  15% residual‑mom                                 │  (existing
        │        │  15% quality+low‑vol                     │   scanner;
        │        │        │  10% PEAD                       │   wire LIVE)
        │        │        │        │  20% cash buffer        │
        │        │        │        │        │                │
   ALL ROUTED THROUGH REGIME OVERLAY (currently DISABLED, v4 ENABLES)
   BULL→tilt momentum; BEAR→tilt quality+low‑vol; TRANSITION→cash buffer up
```

## What changes vs v3

| Aspect | v3.x | v4.0 |
|---|---|---|
| LIVE strategies | 1 (momentum top‑15) | 5 sleeves running concurrently |
| Capital allocation | static 80% gross | rolling‑Sharpe‑weighted, regime‑conditional |
| Regime overlay | computed but DISABLED | computed + APPLIED to gross + sleeve routing |
| Drawdown gates | per‑portfolio (v3.46) | per‑sleeve + per‑portfolio (sleeve‑level decay‑demote already shipped v3.51) |
| Adversarial review | ad‑hoc | mandatory CI gate on every promotion (shipped v3.51) |
| Cross‑sleeve correlation | not measured | measured + alert > 0.70 (shipped v3.51) |
| Expected PIT Sharpe | 0.96 | 1.4–1.6 if uncorrelated‑edges thesis holds |
| Worst expected DD | ‑33% | ‑22 to ‑25% via overlay + correlation gate |

## Sleeves to ship through 3‑gate (in order)

Each must pass survivor 5‑regime → PIT validation → CPCV (PBO < 0.5,
Deflated Sharpe > 0) before LIVE. None ships on intuition. None ships
without adversarial review approval.

### 1. Residual momentum (cleanest first add)
- **Module:** `src/trader/residual_momentum.py` (already exists, code shipped
  v3.15)
- **Status:** PIT FAILED in v3.25 — edge collapsed to zero on honest
  universe. **DO NOT promote based on backtest alone.**
- **What v4 does:** re‑run survivor + PIT with current methodology;
  if it fails, kill‑list it and move to #2. If it passes, target 15% of
  total gross.
- **Reason this might pass now (vs v3.25 fail):** different universe
  composition since v3.25; methodology improvements. Worth one more pass
  before permanent kill.

### 2. Quality + low‑vol factor
- **Module:** new `src/trader/factor_quality_lowvol.py`
- **What:** rank S&P 500 by composite of (ROE, ROA, Piotroski F‑score,
  inverse 60d realized vol). Top‑15 quality + low‑vol names. Captures
  Asness‑Frazzini‑Pedersen Quality Minus Junk + Frazzini‑Pedersen Betting
  Against Beta.
- **Why this likely passes:** factor is well‑validated in academic
  literature; uncorrelated with momentum (~0.2 historical correlation).
  Target 15% of total gross.
- **Data dependency:** needs EdgarTools (per swarm research,
  `dgunning/edgartools` MIT) for fundamentals — ROE / ROA / margins.

### 3. PEAD event sleeve
- **Module:** existing `src/trader/anomalies.py` already has scaffold;
  needs LIVE wiring.
- **What:** post‑earnings‑announcement‑drift — buy on positive surprise,
  hold ~60 days, exit on signal decay or stop. Scope: only S&P 500 names
  with > 5% positive surprise on the day of release.
- **Why this likely passes:** PEAD is one of the oldest documented
  anomalies (Bernard‑Thomas 1989); persistent at multi‑decade timescales.
  Target 10% of total gross.
- **Data dependency:** earnings dates from yfinance + EdgarTools 8‑K
  detection; surprise calculation requires consensus estimates (likely
  needs paid Finnhub at $50/mo or similar).

### 4. Merger‑arb sleeve
- **Module:** existing `src/trader/merger_arb.py` already has scoring;
  needs LIVE wiring with restricted scope.
- **What:** announced‑deal arbitrage. Long target / short acquirer if
  cash+stock; long target only if cash deal. Cap deal size at $1B+ AUM
  for liquidity. Filter for > 70% regulator‑approval probability.
- **Why this likely passes:** small but persistent edge (5–7% annualized
  pre‑costs, ~4–5% post‑costs at retail spread); uncorrelated with
  momentum and quality.
- **Risk:** deal‑break tail risk — a single broken deal can wipe out
  6–12 months of carry. Strict per‑deal sizing cap (1.5%).
- **Target:** 5% of total gross.

## Regime overlay routing (the core paradigm shift)

Currently in v3.49.0: HMM + macro + GARCH compose a `final_mult ∈ [0, 1.2]`
applied to GROSS exposure. **Disabled by default.**

In v4: same multiplier + a NEW per‑sleeve TILT layer:

```python
def route_capital_by_regime(regime: str, base_alloc: dict[str, float]) -> dict[str, float]:
    """Tilt sleeve allocations based on HMM regime classification.

    Bull regime: tilt momentum + residual‑mom UP. Cut quality cushion.
    Transition: hold base allocation. Increase cash buffer.
    Bear regime: tilt quality+low‑vol UP. Cut momentum + residual‑mom HARD.
              PEAD + merger‑arb mostly unchanged (uncorrelated to broad market).
    """
```

Rule‑based, not discretionary. CPCV‑tested before LIVE.

## Risk math vs v3

v3 worst observed PIT DD: ‑33% (top‑15 momentum has 27% concentration
risk; correlated names cluster in tech).

v4 expected worst DD math:
- 35% momentum at –33% worst → –11.5% portfolio contribution
- 15% residual‑momentum at –25% → –3.75%
- 15% quality+low‑vol at –18% → –2.7%
- 10% PEAD at –20% → –2.0%
- 5% merger‑arb at –15% → –0.75%
- Sum if perfectly correlated: –20.7% (down from –33%)
- Sum if uncorrelated (true diversification benefit): ~–12% (geometric)
- Realistic: –18 to ‑23% — **9 to 14pp tighter than v3.x**

PIT Sharpe math (assuming each sleeve passes 3‑gate):
- Single‑sleeve PIT Sharpe: 0.7–1.0 each
- Portfolio Sharpe with 5 uncorrelated sleeves: 1.4–1.6
- This isn't speculation; AQR's QMJ + momentum + value + low‑vol + carry
  stack to ~1.5 over 30+ years (Asness, Moskowitz, Pedersen 2013).

## What this means for the user

- **Same approximate CAGR** (~17–19%) but **2x lower drawdown risk**
  (–18% vs –33%) and **higher Sharpe** (1.4 vs 0.96).
- **Same monthly rebalance cadence** — NOT a paradigm shift to higher
  frequency. Still boring. Still patient.
- **Same Roth IRA target.** Still tax‑advantaged.
- **Behavioral pre‑commit unchanged.** Still no manual override after
  –15% DD.
- **5 sleeves to monitor** instead of 1. Sleeve health monitor (v3.51)
  + dashboard (v3.52) make this tractable.

## What this DOESN'T do (deliberately)

- **No higher frequency.** Strategy stays monthly. The literature
  (FINSABER, Lopez‑Lira) is unanimous: retail can't compete on speed.
- **No multi‑asset.** Equities only. Bonds + commodities = v5 conversation.
- **No discretionary override.** All rule‑based, all CPCV‑tested.
- **No LLM stock‑picking.** Verified‑failed pattern. LLMs stay in
  narrative + post‑mortem + adversarial review roles.
- **No options or barbell wiring.** Defer until live equity > $50k.

## Effort estimate

| Phase | Work | Effort |
|---|---|---|
| Research spike | This doc + design review | 2h (done) |
| Sleeve 1 (residual‑mom) re‑validation | Re‑run survivor + PIT + CPCV | 4h |
| Sleeve 2 (quality+low‑vol) build | New module + 3‑gate validation | 8h |
| Sleeve 3 (PEAD) wiring | LIVE adapter + earnings data integration | 16h |
| Sleeve 4 (merger‑arb) wiring | LIVE adapter + per‑deal sizing | 8h |
| Regime overlay routing | New `route_capital_by_regime` + CPCV | 4h |
| Meta‑allocator wiring to LIVE | Replace single‑LIVE with rolling‑Sharpe routing | 4h |
| Dashboard updates | Per‑sleeve performance + correlation matrix | 4h |
| Tests + docs + commit | Full suite + V4 release notes | 6h |
| **Total** | | **~56h focused work** |

Realistic timeline at part‑time pace: 2–3 weeks dedicated, or 4–6 weeks
alongside other work.

## Promotion gates (no LIVE without ALL)

Each sleeve, in order, must:
1. Pass survivor 5‑regime backtest (Sharpe wins ≥ 4/5)
2. Pass PIT validation (Sharpe drop < 30% on honest universe)
3. Pass CPCV (PBO < 0.5; deflated Sharpe > 0)
4. Pass adversarial review (`adversarial_review.py`, shipped v3.51)
5. Pass override‑delay 24h cool‑off after merge
6. Run as shadow for ≥30 days collecting LIVE evidence
7. Independent reviewer + spousal pre‑brief sign‑off (per `BEHAVIORAL_PRECOMMIT.md`)

Any failure = kill‑list entry in `docs/CRITIQUE.md`. No retry without
new theory or new data.

## What stays the same as v3.x

- 4‑layer defense (code / custodian / human / document)
- 3‑gate promotion methodology
- Override‑delay 24h cool‑off
- Peek counter (>3/30d alert)
- Deployment anchor + freeze states
- Behavioral pre‑commit (signed before LIVE arming)
- LLM verifier on every agent output
- Roth IRA target, monthly rebalance, paper‑first

v4 changes the STRATEGY. Not the safety architecture. The safety
architecture is the only reason a paradigm shift like this is even
possible — you can ship a more aggressive system because the gates
stop you from blowing up.

## Open questions for the user

1. **Approve the multi‑sleeve thesis as v4 direction?** If no, what shift
   would you prefer (self‑improving, multi‑asset, causal‑inference)?
2. **Order of sleeves?** Default: residual‑mom → quality+low‑vol → PEAD
   → merger‑arb. If you have a preference (e.g., merger‑arb first because
   it's most uncorrelated), say so.
3. **Earnings‑consensus data:** Finnhub paid tier ($50/mo) for PEAD?
   Or skip PEAD until we have the data infrastructure?
4. **Roth IRA timing:** v4 development happens during paper‑trading
   period. LIVE arm with v4.0 multi‑sleeve rather than v3 single‑sleeve
   when 90‑day clock completes?

Answers to (4) determine the build sequence: if YES, sleeves 1–4 must
all pass 3‑gate by ~day 75 of the live‑arm clock. Tight but feasible.
