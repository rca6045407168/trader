"""Adversarial pre-promotion review (v3.51.0 / Tier B).

When a shadow variant is proposed for promotion to LIVE, this module spawns
a Claude agent in adversarial mode ("find what's wrong with this strategy")
before the promotion can be merged. Output is gated by `agent_verifier.py`.

Why this exists:
  - The 3-gate pipeline (survivor → PIT → CPCV) catches statistical fragility,
    but doesn't catch implementation bugs, look-ahead leaks, or semantic
    issues with the variant function code.
  - v3.27 caught a real kill-switch bug via independent reviewer; we want to
    formalize that pattern as a CI requirement.
  - Per docs/SWARM_VERIFICATION_PROTOCOL.md, any LLM output feeding a trading
    decision must pass TRUST/VERIFY/ABSTAIN.

Usage:
    from trader.adversarial_review import review_promotion
    result = review_promotion(
        variant_id="new_candidate_v1",
        proposed_status="live",
        diff_summary="...the code change being merged...",
    )
    if result.recommendation == "BLOCK":
        raise SystemExit(f"adversarial review blocked: {result.reasons}")

Returns: ReviewResult with recommendation in {"APPROVE", "REVIEW", "BLOCK"}.

The verifier ABSTAINS if Claude's response contains fabricated citations,
anonymous-author claims, or extreme Sharpe claims. ABSTAIN → BLOCK by
conservative default (we'd rather not promote than promote based on a
hallucinated review).

This is the formalization of the 'one-line adversarial review' that v3.27
did manually. Now mandatory for any variant_id status change live <-> shadow.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Anthropic API key (re-uses the same env that critic + narrative use)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("ADVERSARIAL_REVIEW_MODEL", "claude-opus-4-7")


ADVERSARIAL_PROMPT = """You are an adversarial reviewer for a personal automated equity trading
system. Your ONLY job is to find reasons NOT to promote this variant to LIVE.
If you find none, you must say so explicitly — BUT before saying "approve",
verify each of these checks against the description and parameters:

  1. **Look-ahead bias** — does the variant use any data that wouldn't be
     available at decision time? E.g., current short interest (point-in-time
     only), forward-looking earnings estimates, point-in-time fundamentals
     not yet released.
  2. **Survivor bias** — does the candidate universe filter on data only
     visible after the fact (e.g., "top market cap today")?
  3. **Concentration risk** — does any single name exceed our 16% position
     cap? Any sector exceed 35%?
  4. **Implementation cliff** — does the variant fall back to a degenerate
     state (full cash, zero positions) under predictable conditions, then
     fail to re-enter? Reference v3.5 lesson: VIX-cut variants miss V-shape
     recoveries.
  5. **Capacity** — does the variant size positions in a way that would
     blow up at $25k AUM? At $250k? Round-lot constraints?
  6. **PIT validation** — the description claims PIT validation. Does the
     `pit_validated` parameter say True? What is `pit_sharpe`?
  7. **CPCV result** — has the variant passed Combinatorial Purged Cross-
     Validation per Lopez de Prado? PBO < 0.5? Deflated Sharpe > 0?
  8. **Cost realism** — the variant's expected returns net of: notional-
     order slippage at 5-10 bps, monthly turnover at 80%, and a $50 minimum
     order size?
  9. **Re-entry / kill-switch interaction** — does the variant interact
     poorly with our existing freeze states (-25% deploy DD, -33%
     liquidation gate)?
 10. **Honest about edges** — does the description claim Sharpe > 2.0,
     CAGR > 30%, or drawdown < 10%? Those are red flags. Honest expected
     PIT Sharpe is 0.8-1.2, CAGR 15-20%, drawdown 25-35%.

If a check FAILS, recommend **BLOCK** with the specific check number.
If a check is INCONCLUSIVE, recommend **REVIEW** with the missing data.
If all checks pass, recommend **APPROVE**.

VERIFICATION REQUIREMENTS (per docs/SWARM_VERIFICATION_PROTOCOL.md):
- Cite every specific claim with the section of the description, the
  parameter name, or the diff line.
- Do NOT cite academic papers unless you can give a verbatim quote.
- If you don't have enough information to evaluate a check, SAY SO; don't
  fabricate. Refusing to fabricate earns trust.

OUTPUT FORMAT (strict):
```
RECOMMENDATION: APPROVE | REVIEW | BLOCK
CHECKS_PASSED: <list of #s>
CHECKS_FAILED: <list of #s with specific reason per fail>
CHECKS_INCONCLUSIVE: <list of #s with missing data per check>
NOTES: <free-form one paragraph>
```
"""


@dataclass
class ReviewResult:
    variant_id: str
    recommendation: str  # APPROVE | REVIEW | BLOCK
    checks_passed: list[int] = field(default_factory=list)
    checks_failed: list[dict] = field(default_factory=list)  # [{check: 3, reason: "..."}]
    checks_inconclusive: list[dict] = field(default_factory=list)
    notes: str = ""
    raw_response: str = ""
    verifier_action: str = ""  # TRUST | VERIFY | ABSTAIN
    verifier_reasons: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error: Optional[str] = None


def _parse_response(text: str) -> dict:
    """Parse the structured RECOMMENDATION block from Claude's response.
    Returns dict with the 4 keys; falls back to BLOCK if unparseable.
    """
    out = {
        "recommendation": "BLOCK",
        "checks_passed": [],
        "checks_failed": [],
        "checks_inconclusive": [],
        "notes": "(parser couldn't extract structured response — default-deny)",
    }
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("RECOMMENDATION:"):
            v = line.split(":", 1)[1].strip().upper()
            if v in ("APPROVE", "REVIEW", "BLOCK"):
                out["recommendation"] = v
        elif line.startswith("CHECKS_PASSED:"):
            try:
                out["checks_passed"] = [int(x.strip()) for x in line.split(":", 1)[1].split(",") if x.strip().isdigit()]
            except Exception:
                pass
        elif line.startswith("NOTES:"):
            out["notes"] = line.split(":", 1)[1].strip()
    return out


def review_promotion(variant_id: str, proposed_status: str,
                     description: str, params: Optional[dict] = None,
                     diff_summary: str = "") -> ReviewResult:
    """Spawn an adversarial Claude review. Returns ReviewResult.

    Default recommendation is BLOCK if the API call fails or the verifier
    abstains on the response — conservative default for a promotion gate.
    """
    result = ReviewResult(variant_id=variant_id, recommendation="BLOCK")

    if not ANTHROPIC_API_KEY:
        result.error = "ANTHROPIC_API_KEY not set; defaulting to BLOCK"
        result.notes = result.error
        return result

    try:
        from anthropic import Anthropic
    except ImportError:
        result.error = "anthropic package not installed; defaulting to BLOCK"
        result.notes = result.error
        return result

    user_msg = (
        f"Variant under review: `{variant_id}`\n"
        f"Proposed status change: -> `{proposed_status}`\n\n"
        f"Description:\n{description}\n\n"
        f"Parameters:\n{params or {}}\n\n"
        f"Diff summary (if provided):\n{diff_summary or '(none)'}\n\n"
        "Apply the 10 adversarial checks. Output the structured response."
    )

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=ADVERSARIAL_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text
        result.raw_response = raw
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.notes = result.error
        return result

    # Verify via agent_verifier
    try:
        from .agent_verifier import verify_citations
        v = verify_citations(raw)
        result.verifier_action = v.action.value if hasattr(v.action, "value") else str(v.action)
        result.verifier_reasons = v.reasons or []
    except Exception as e:
        result.verifier_action = "verifier_unavailable"
        result.verifier_reasons = [f"{type(e).__name__}: {e}"]

    if result.verifier_action == "abstain":
        # Abstain → conservative BLOCK
        result.recommendation = "BLOCK"
        result.notes = ("agent_verifier ABSTAINED on review output — "
                        "conservative default-deny per SWARM_VERIFICATION_PROTOCOL")
        return result

    parsed = _parse_response(raw)
    result.recommendation = parsed["recommendation"]
    result.checks_passed = parsed["checks_passed"]
    result.checks_failed = parsed["checks_failed"]
    result.checks_inconclusive = parsed["checks_inconclusive"]
    result.notes = parsed["notes"]
    return result
