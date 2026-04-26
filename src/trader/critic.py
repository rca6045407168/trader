"""Bull / Bear / Risk-Manager swarm debate using the Claude API.

For every bottom-catch candidate, three agents argue. The Risk Manager reads
both cases and decides BUY or SKIP, plus a position size. Bottom-catches are
risky — the swarm exists to cull the bad ones.

Momentum picks DON'T go through the swarm: that strategy is rule-based, has
decades of out-of-sample evidence, and adding LLM judgment would just add noise.
"""
from dataclasses import asdict
from anthropic import Anthropic

from .config import CRITIC_MODEL
from .strategy import Candidate

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


BULL_SYS = """You are the Bull. Your job: argue why this oversold-bounce trade should be taken.
Cite the specific technicals (RSI, Bollinger z-score, volume, trend filter).
Name ONE catalyst that could trigger the bounce.
Be terse — 4 sentences max. End with: CONFIDENCE: <0-100>
"""

BEAR_SYS = """You are the Bear. Your job: argue why this trade should NOT be taken.
Cite real risks: macro overhang, sector weakness, falling-knife pattern, earnings ahead.
Name the SPECIFIC scenario that would invalidate the bounce thesis.
Be terse — 4 sentences max. End with: CONFIDENCE: <0-100> (higher = more confident this is a bad trade)
"""

RISK_SYS = """You are the Risk Manager. You've read the bull case and the bear case.
Decide: BUY or SKIP.
If BUY, recommend a position size as % of portfolio (cap 5%).
If SKIP, name the trade-killer in one phrase.
Be terse — 5 sentences max.

Format your final line EXACTLY as one of:
  DECISION: BUY <pct>%
  DECISION: SKIP
"""


def _ask(system: str, user: str, max_tokens: int = 400) -> str:
    client = _get_client()
    resp = client.messages.create(
        model=CRITIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def debate(candidate: Candidate, market_context: str = "") -> dict:
    """Run the three-agent debate. Returns dict with bull/bear/decision text and parsed action."""
    user_brief = (
        f"Trade: {candidate.action} {candidate.ticker}\n"
        f"Style: {candidate.style}\n"
        f"Composite score: {candidate.score:.3f}\n"
        f"ATR (% of price): {candidate.atr_pct:.2%}\n"
        f"Signal components: {candidate.rationale}\n"
        f"Market context: {market_context or 'none provided'}"
    )

    bull = _ask(BULL_SYS, user_brief)
    bear = _ask(BEAR_SYS, user_brief)

    risk_brief = (
        f"Trade: BUY {candidate.ticker}  ({candidate.style}, score={candidate.score:.3f}, atr={candidate.atr_pct:.2%})\n"
        f"\n--- BULL CASE ---\n{bull}\n"
        f"\n--- BEAR CASE ---\n{bear}\n"
        f"\nDecide."
    )
    decision_text = _ask(RISK_SYS, risk_brief, max_tokens=300)

    parsed = _parse_decision(decision_text)

    return {
        "ticker": candidate.ticker,
        "candidate": asdict(candidate),
        "bull": bull,
        "bear": bear,
        "decision_text": decision_text,
        "action": parsed["action"],
        "position_pct": parsed["position_pct"],
    }


def _parse_decision(text: str) -> dict:
    """Parse 'DECISION: BUY 3%' or 'DECISION: SKIP' from the risk manager's output."""
    last_line = next((l for l in reversed(text.splitlines()) if "DECISION:" in l.upper()), "")
    upper = last_line.upper()
    if "SKIP" in upper:
        return {"action": "SKIP", "position_pct": 0.0}
    if "BUY" in upper:
        # extract %
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", last_line)
        pct = float(m.group(1)) / 100 if m else 0.02
        return {"action": "BUY", "position_pct": min(pct, 0.05)}
    return {"action": "SKIP", "position_pct": 0.0}
