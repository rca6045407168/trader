# Round-2 Synthesis — Five New Docs Mapped to v5

*Cross-cutting summary of the Round-2 advisory swarm (May 2026). Routes the five new docs into the existing v5 build sequence, flags contradictions with Round-1 (V5_ALPHA_DISCOVERY_PROPOSAL), and gives a single prioritized to-do list.*

---

## What Round-2 produced

Five new docs, each from a distinct expert lens that Round-1 didn't deploy:

| Doc | Lens | Key contribution |
|---|---|---|
| [RISK_FRAMEWORK.md](RISK_FRAMEWORK.md) | CRO / risk officer | Per-sleeve gross caps, factor exposure budgets, scenario-conditional sizing rules, four-threshold drawdown protocol with explicit response actions, time-under-water tracking |
| [ADVERSARIAL_THREAT_MODEL.md](ADVERSARIAL_THREAT_MODEL.md) | Red-team / offensive security | PyPI supply chain as the realistic attack chain, prompt injection on LLM-in-path, mitigation order with ~25 hours of free tooling |
| [TAIL_RISK_PLAYBOOK.md](TAIL_RISK_PLAYBOOK.md) | Catastrophe modeler / actuary | EVT / GPD / POT for VRP sizing, return-period thinking, ergodicity argument, recommendation to tighten VRP from 15% → 10–12% baseline |
| [FUND_FAILURE_CASE_STUDIES.md](FUND_FAILURE_CASE_STUDIES.md) | Financial historian | 12 case studies mapped to mechanism classes, five lessons most applicable to v5 retail scale, single book to re-read before LIVE |
| [INFORMATION_THEORY_ALPHA.md](INFORMATION_THEORY_ALPHA.md) | Information theorist / Bayesian | Mutual-information pre-screen for sleeves, McLean-Pontiff Bayesian prior for promotion, MI half-life decay tracking as the missing alpha-decay gate |

Plus two supplementary files the historian agent saved independently — `FUND_FAILURE_FORENSICS.md` (extended case-study detail) and `V5_DEPLOYMENT_RISK_SYNTHESIS.md` (deployment-gate spec that overlaps Round-1 + RISK_FRAMEWORK). Treat those as appendices; the canonical case-study reference is `FUND_FAILURE_CASE_STUDIES.md`.

---

## Where Round-2 contradicts Round-1

Three real contradictions worth resolving before Claude Code ships v5.

**Contradiction 1 — VRP allocation.** V5_ALPHA_DISCOVERY_PROPOSAL.md proposes 15% to VRP. TAIL_RISK_PLAYBOOK.md recommends 10–12% baseline with a regime-conditional taper to 5–7% when realized vol is in the upper half of its 5-year distribution. The tail-risk doc has the better argument because it's grounded in EVT and ergodicity, both of which the original proposal handwaved. **Resolution: adopt the tail-risk recommendation. Reduce v5 baseline VRP allocation to 12% with the regime taper rule. The momentum core moves up to 53% to absorb the 3pp difference.**

**Contradiction 2 — research velocity vs over-engineering.** Round-1's architect agent argued for ~93 hours of research-velocity infrastructure (feature store, virtual shadows, etc.); the BLINDSPOTS.md bear case (section 8) said this is over-engineering at $10k AUM. Round-2's RISK_FRAMEWORK adds another ~32 hours of risk infrastructure. ADVERSARIAL_THREAT_MODEL adds ~25 hours of security mitigations. INFORMATION_THEORY_ALPHA adds ~16 hours. **Total Round-2 incremental work: ~73 hours on top of v5's existing ~97 hours.** That pushes total v5 budget toward 170 hours.

The honest framing from BLINDSPOTS.md still applies: 170 hours of work for ~$400-800/year of expected return improvement at $10k AUM is a brutal opportunity-cost calculation against the operator's primary work time. **Resolution: keep v5 build time-boxed. Mandatory pre-LIVE: the security Days-1-3 mitigations (3 hours), the four-threshold drawdown protocol from RISK_FRAMEWORK (4 hours), the GPD tail-VaR gate from TAIL_RISK_PLAYBOOK (10 hours), and the MI pre-screen from INFORMATION_THEORY_ALPHA (4 hours). The rest is post-LIVE iteration. Total mandatory pre-LIVE addition: ~21 hours, not 73.**

**Contradiction 3 — kill-switch threshold.** Existing system kills at portfolio -8% on 180d window. RISK_FRAMEWORK adds three more thresholds (-5% yellow, -12% escalation, -15% catastrophic). TAIL_RISK_PLAYBOOK implies -8% may be too tight if the tail distribution is fat enough that 1-in-100-year events naturally produce -10 to -15% portfolio loss. **Resolution: keep -8% as the existing red-alert threshold and add the -5/-12/-15 protocol *as additional response actions*, not as relaxations. The framework gets stricter, not looser. Document explicitly that -8% is the existing kill, not a new one.**

---

## Where Round-2 confirms Round-1

The most important methodological consensus: **the existing 3-gate (survivor → PIT → CPCV) is information-theoretically sound and should not be loosened**. INFORMATION_THEORY_ALPHA.md explicitly argues that CPCV detects MI generalization gap, PIT detects MI universe robustness, and survivor stress detects MI persistence across regimes. You arrived at the right framework via pragmatism; the math agrees.

The second consensus: **the killed shadows (residual mom v3.15, vol-targeted v3.16, crowding v3.21, multi-asset trend v3.19, quality+low-vol v3.20, regime overlay 3.7-3.36) were *correctly* killed**. Round-2 reframes their failure modes — survivorship inflation, look-ahead-via-vol-targeting, regime non-stationarity, fat-tail crowding — but agrees they should be killed. Don't re-test without genuinely new data or theory.

The third consensus: **stop stacking equity factors; add structurally-different alpha sources (VRP, FOMC, ML-PEAD)**. Round-1's economist argued this. Round-2's information theorist confirmed it via mutual-information independence. Round-2's historian confirmed it via the slow-alpha-decay pattern in the Tiger / GLG / ARKK case class.

---

## The single prioritized to-do list

Combining all Round-1 + Round-2 inputs into one sequence. Items in **bold** are mandatory before any v5 sleeve goes LIVE. Items in regular weight are post-LIVE polish.

### Block A — pre-LIVE mandatory (~30 hours)

1. **Days-1-3 security mitigations** (3h): audit `git log -p .env`, rotate Alpaca / Anthropic / Finnhub keys, enable Alpaca account alerts, generate `requirements.txt`, enable GitHub native secret-scanning.
2. **Hash-pinned dependencies** (2h): `pip-tools` with `--require-hashes`. Mitigates the realistic PyPI attack chain.
3. **Four-threshold drawdown protocol** (4h): extend `kill_switch.py` with -5/-8/-12/-15 thresholds and the response actions from RISK_FRAMEWORK section 6.
4. **GPD tail-VaR gate** (10h): wire `pyextremes` or scipy GPD fitting on rolling 252-day returns; gate VRP entry on ES(99.5%) > -2% threshold (TAIL_RISK_PLAYBOOK section 8).
5. **MI pre-screen** (4h): one-pass NumPy script computing fixed-bin MI on each candidate sleeve's signal-and-return pair. Gate at 0.001 nats binary-MI; below this, kill before CPCV (INFORMATION_THEORY_ALPHA section 7).
6. **Cron heartbeat alert** (2h): "did the daily run actually fire" check that emails if missing. Trivial; eliminates a known operational blindspot.
7. **VRP allocation reduction to 12% baseline + regime taper** (1h config change): adopt the tail-risk doc's recommendation; document in V5_ALPHA_DISCOVERY_PROPOSAL.md.
8. **LLM input sanitization** (4h): regex guard for instruction-pattern markers in any data passed to `copilot.py` / `adversarial_review.py` / `postmortem.py`. Mitigates prompt-injection class.

### Block B — pre-LIVE strongly recommended (~30 hours)

9. **Per-sleeve gross caps and factor exposure budgets** (8h): wire RISK_FRAMEWORK section 2 into `risk_manager_v5.py`. Defends against the Amaranth pattern.
10. **Scenario-conditional sizing rules** (6h + 6h CPCV): wire allocation rules from RISK_FRAMEWORK section 4; CPCV-validate on Tier-1 scenarios.
11. **Tier 1 stress regimes** (per SCENARIO_LIBRARY): expand REGIMES tuple from 5 to 9; gate v5 promotion on all-pass.
12. **Knight-Capital deployment gate** (4h): pre-deploy fixture replay for any change to `main.py` or `execute.py`. Required diff-within-tolerance check before merge to `main`.
13. **MI cross-sleeve independence check** (6h): compute pairwise MI across momentum / VRP / FOMC / PEAD signals; abort v5 if any pair > 0.5.

### Block C — post-LIVE (~50 hours)

14. Pairwise MI half-life monitoring weekly (6h)
15. Tier 2 + Tier 3 + scripted scenarios from SCENARIO_LIBRARY (12h)
16. Property-based testing on VRP invariants via Hypothesis (8h)
17. TCA / live fill audit on momentum sleeve (6h)
18. Mutation testing baseline via mutmut (4h)
19. Schema-strict Pandera validation (8h)
20. External human review of methodology (4h engagement)
21. Public pre-registration of v5 results (2h)

### Block D — explicitly defer

The architect's full feature-store proposal, the v3.44 OTM call barbell (already approved-deferred), the operator thesis-ledger sleeve (paradigm-shift but needs separate v5.x cycle, not v5.0), the SPA / White's Reality Check on the variant cohort (worth 12h but not pre-LIVE).

---

## Total v5 effort, honestly

- **Block A (mandatory pre-LIVE):** ~30 hours.
- **Block B (strongly recommended pre-LIVE):** ~30 hours.
- **Original v5 build (sleeves, infrastructure):** ~97 hours per V5_ALPHA_DISCOVERY_PROPOSAL.
- **Block C (post-LIVE polish):** ~50 hours.

Realistic minimum to ship LIVE-ready v5: ~127 hours (Block A + B + original v5 build). At 10 hours/week part-time pace, 12-13 weeks. Tighter timelines require dropping Block B items to post-LIVE (Block C), which is acceptable but accumulates risk.

The brutal reframe from BLINDSPOTS.md still applies: ~127 hours on a $10k Roth at +0.4 expected Sharpe lift produces ~$400-800/year of additional return. The same hours on the operator's primary work at pre-seed produce orders of magnitude more value. The honest framing of v5 remains: this is a learning / discipline / hobby asset, valuable for what it teaches but not a wealth-creation asset until it scales beyond the IRA cap with multi-year LIVE Sharpe evidence.

---

## What Richard should answer before Claude Code starts

The five questions in V5_ALPHA_DISCOVERY_PROPOSAL section "Open questions" remain open. Round-2 adds three more:

1. **Adopt the tail-risk doc's VRP reduction (15% → 12% baseline with regime taper)?** Recommended yes.
2. **Adopt the four-threshold drawdown protocol (-5/-8/-12/-15)?** Recommended yes; existing -8% is preserved as the red-alert threshold.
3. **Time-box v5 to ~127 hours total (Block A + B + original v5) with hard kill if it overruns by 50%?** This is the BLINDSPOTS.md recommendation refined with Round-2 evidence. A research project that overruns its budget is signaling something wrong with the underlying premise.

Once those three plus the five Round-1 questions are answered, the prioritized list above is implementable directly. Each block ships as a separate PR with the existing 3-gate validation; no shortcuts.

---

## The meta-insight from Round-2

Round-1 surfaced the *strategy* paradigm shift: stop stacking equity factors, add structural premia (VRP) and behavioral edge (FOMC) and ML-augmented behavioral (PEAD). Round-2 surfaced the *operational* paradigm shift: a four-sleeve portfolio is not 4× as hard to manage as a one-sleeve portfolio — it's 9–12× as hard, because every cross-sleeve correlation is a new monitoring surface, every factor budget is a new constraint, every shared dependency is a new failure mode.

The two paradigm shifts pair: the strategy gain (Round-1's +0.4 Sharpe lift) is real, but only realizes if the operational discipline scales correspondingly. The CRO framework, EVT tail discipline, security hygiene, MI gates, and case-study lessons are the operational complement to Round-1's strategy work. Without them, v5 ships a 1.10 Sharpe wearing a 1.50 mask, and the difference shows up the first time an actual stress event hits.

Ship both halves. The operational pieces are mostly free in tooling, mostly cheap in time, and indispensable in structure.

---

*Last updated 2026-05-04. Status: synthesis. Companion to V5_ALPHA_DISCOVERY_PROPOSAL.md, SCENARIO_LIBRARY.md, BLINDSPOTS.md, TESTING_PRACTICES.md, RISK_FRAMEWORK.md, ADVERSARIAL_THREAT_MODEL.md, TAIL_RISK_PLAYBOOK.md, FUND_FAILURE_CASE_STUDIES.md, INFORMATION_THEORY_ALPHA.md.*
