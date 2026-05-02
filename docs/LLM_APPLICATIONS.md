# LLM Applications in the Trader — Honest Assessment

Created v3.47 after a 4-agent swarm researched LLM-trading literature.
Findings synthesized + verified per `SWARM_VERIFICATION_PROTOCOL.md`.

## Headline finding (verified across 4 agents)

**LLM-as-PM doesn't work at retail. LLM-as-analyst-intern might.**

Specifically:
- 95%+ of LLM-trading papers claiming Sharpe > 2.0 have methodological errors
  (look-ahead via training cutoff, survivorship bias, no LLM API cost modeling)
- The genre's universal failure mode: GPT-4 trained in 2024 backtested on
  2018-2022 data — model has read the answer key
- LLM API cost at retail scale ($50-500/day for multi-agent debate over 500
  names) eats most claimed alpha

Verified-real reference papers:
- arxiv 2505.07078 (Li et al.) — FINSABER: shows LLM trading "advantages
  deteriorate significantly under broader cross-section and longer-term
  evaluation." Anti-BS methodology paper.
- arxiv 2502.16789 (Tang) — AlphaAgent: only credible positive result
  found, IR ~1.05 over 4 years on S&P 500 (matches our honest +0.96 baseline,
  not dwarfing it).
- arxiv 2304.07619 (Lopez-Lira) — alpha concentrated in untradeable
  small-cap negative-news drift; explicitly notes "strategy returns decline
  as LLM adoption rises."

## What we WILL NOT build

These have prior probability of failing CPCV >80% per swarm consensus:

- ❌ LLM-driven full trading agent (TradingGPT, FinAgent, etc.)
- ❌ GPT-based "stock recommender" portfolio
- ❌ Multi-agent LLM debate over picks (cost > alpha at our scale)
- ❌ Daily LLM-based rebalance (latency disadvantage vs systematic players)

## What we MIGHT build (with verification gates)

Where LLMs PLAUSIBLY add value, ranked by EV-per-cost:

### 1. Earnings call summarization as a momentum-rank tiebreaker

Use Claude/GPT to extract 3-bullet summary from each held name's most recent
earnings call. **Not as a trading signal directly** — as a feature feeding
the existing momentum rank. Specifically:
- Among top-15 momentum picks, demote any name whose latest earnings call
  contains negative-tone language not yet in the price (Glasserman 2309.17322
  shows this is real, with caveat about ticker anonymization)
- Cost: ~$0.02 per name × 15 names × 4 calls/year = **$1.20/year**. Trivial.

### 2. Operational automation (LLM as ops intern)

- Tax-loss harvesting candidate identification (when account moves to taxable)
- Wash-sale check before any rebalance
- Variant naming + auto-documentation for new shadow strategies
- Daily report narrative writing (already doing this — `narrative.py`)

### 3. Adversarial code review for variant changes

When we propose a new LIVE variant, spawn an LLM agent in adversarial mode
("find what's wrong with this strategy") BEFORE shipping. Already partially
done in v3.27 (independent reviewer caught kill-switch bug). Could be
formalized as a CI gate.

### 4. Per-stock options data extraction (REQUESTED 2026-05-02)

Read each held name's option chain and extract:
- Implied vol vs realized vol gap (overpriced options = candidate for sleeve)
- Skew (put-call IV differential) as positioning signal
- Open interest concentration as flow indicator

NOT yet built. Requires verification per protocol before deployment.

## What we currently use that's LLM-related

| Feature | LLM model | Status |
|---|---|---|
| Daily report narrative | Claude (`claude-sonnet-4-6`) | LIVE |
| Web-search for current events in narrative | Claude with web_search tool | LIVE |
| Anomaly scan rationale | Claude | LIVE |
| Postmortem analysis | Claude (`claude-opus-4-7`) | LIVE |
| 20-agent debate on bottom-catch picks | Claude | LIVE (USE_DEBATE=true) |
| Variant proposal / strategy generation | Claude (manually invoked) | LIVE (us, in this session) |

## Verification protocol enforcement

Per `SWARM_VERIFICATION_PROTOCOL.md`: any LLM agent output that informs a
trading decision must pass through `agent_verifier.py`:

```python
from trader.agent_verifier import verify_citations
result = verify_citations(agent_output)
if result.action == "abstain":
    raise ValueError(f"Output untrustworthy: {result.reasons}")
elif result.action == "verify":
    # Sample 1-2 citations and WebFetch them
    sample = sample_for_manual_check(result, n=2)
    # ... verify ...
```

## The single biggest improvement v3.47 makes

**The verifier auto-caught fabricated citations in agent 2's output today.**
Without the gate, I would have built a "speaker-weighted FinBERT" feature
based on a paper with "Anonymous" authors (= fabricated). With the gate,
the output was abstained automatically.

This protocol now applies to all future LLM-derived recommendations,
including the per-stock options/bonds research the user requested 2026-05-02.
