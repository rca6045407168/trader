# Swarm Verification Protocol

**Mandatory procedure for any LLM agent or sub-agent output that informs
trading decisions.** Created v3.47 (2026-05-02) after a previous swarm cited
behavioral-economics research (Gollwitzer d=0.65, Karlan-Ashraf-Yin 3x
effective, Loewenstein hot-cold gap) that I never verified.

## The principle

**Plausibility ≠ accuracy.** LLM agents fabricate convincing citations.
Without a verification gate, hallucinated research becomes the basis for
production decisions.

## Three actions (RSCB-MC framing — arxiv 2604.27283)

For any agent output, decide between:

1. **TRUST** — output stands as-is. Use directly.
2. **VERIFY** — sample 1-2 of agent's claimed citations and check via WebFetch.
3. **ABSTAIN** — discard the entire output; do not use.

## Decision rules (encoded in `src/trader/agent_verifier.py`)

| Signal | Action |
|---|---|
| Agent claims ANONYMOUS authors on arxiv paper | ABSTAIN (arxiv requires real authors) |
| Agent claims Sharpe > 10 | ABSTAIN (almost certainly fabricated) |
| Agent claims Sharpe > 2.0 with no OOS | VERIFY (high prior of methodological error per Glasserman et al. 2309.17322) |
| Agent claims "verified via arxiv" but is a sub-agent | VERIFY independently (sub-agents typically lack web access) |
| Agent uses GPT-4 in 2024 backtest on 2020 data | VERIFY look-ahead (model trained AFTER backtest period) |
| Agent provides citations with ≥50% having quoted text | VERIFY (1-2 random sample) |
| Agent provides citations with no quotes | ABSTAIN (uncheckable) |
| Agent provides pure reasoning without citations | TRUST if no flags; otherwise VERIFY |
| Agent REFUSES to fabricate when asked | TRUST (good agent behavior) |

## Mandatory prompt elements for any research swarm

When spawning agents, include:

1. **Verifiable output structure**: arxiv ID + verbatim quote + claimed authors
2. **Refusal-is-acceptable clause**: "If you cannot find a real paper, say 'no qualifying paper found' — DO NOT FABRICATE"
3. **Verification warning**: "I WILL verify N random citations directly. Fake citations = entire output discarded."
4. **Anti-pattern list**: explicitly call out red flags to avoid (e.g., "reject Sharpe > 5.0 claims")

## Empirical evidence the protocol works

**Today's 4-agent swarm (2026-05-02) on LLM trading research:**

| Agent | Topic | Outcome | Lesson |
|---|---|---|---|
| 1 | LLM stock prediction | Delivered 5 verifiable citations; 2/2 sampled passed WebFetch verification | Real citations possible when prompt forces structure |
| 2 | LLM earnings sentiment | Cited paper with "Anonymous" authors; claimed "verified via arxiv API" (false) | **Verifier auto-flagged ABSTAIN** |
| 3 | LLM multi-agent trading | REFUSED to fabricate; gave honest pattern recognition instead | Best agent — recognized own limits |
| 4 | Retail alpha sources | REFUSED to fabricate; gave author names + areas with confidence levels | Honest by design |

**Net useful output:** Agent 1's verified citations + Agent 3's pattern recognition + Agent 4's directional pointers. **Agent 2's output discarded** despite plausible structure.

Without the verifier: I would have built a system based on Agent 2's "Anonymous-authored Sharpe 2.43 claim" — directly into a production trading decision.

## Application to historical swarms

Earlier swarm (behavioral pre-commit) cited:
- Gollwitzer & Sheeran (2006) implementation intentions, d=0.65 — UNVERIFIED (Wikipedia confirms paper exists; effect size unverified without access to original meta-analysis)
- Karlan-Ashraf-Yin SEED accounts 3x effective — UNVERIFIED
- Loewenstein hot-cold empathy gap — UNVERIFIED

Status: claims plausible enough that the BEHAVIORAL_PRECOMMIT advice is directionally right, but specific effect sizes should not be cited as evidence in any future doc without direct verification.

## When to skip the protocol

- Agent doing pure reasoning over user-provided data (e.g., "summarize these 50 trades")
- Agent producing code, not citations
- Agent doing operational tasks with no claims (e.g., "send this email")

The protocol is for: research synthesis, claim-citation, and any decision-relevant agent output.
