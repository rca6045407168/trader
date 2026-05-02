"""LLM-agent output verification gate.

Inspired by RSCB-MC (Risk-Sensitive Contextual Bandits for Memory Retrieval,
arxiv 2604.27283). Reframes "should I trust this agent's output?" as a
risk-sensitive decision: trust / abstain / verify, based on observable
features of the output.

Why this exists:
  - Earlier swarm agent on behavioral pre-commit cited Gollwitzer (d=0.65),
    Karlan-Ashraf-Yin (3x effective), Loewenstein hot-cold gap. I never
    verified these. Plausibility ≠ accuracy.
  - Today's swarm: 2 of 4 agents REFUSED to fabricate; 1 delivered real
    citations (verified); 1 delivered citations that included an "Anonymous"
    arxiv author — high-risk signal.

Verification policy:
  Trust outright IF: zero verification needed (e.g., agent doing pure
  reasoning over user-provided data).
  Verify a sample IF: agent makes citable claims (papers, statistics).
  Abstain entirely IF: claimed quotes don't exist OR author is "Anonymous"
  on a published paper OR claimed effect size has no meta-analysis backing.

Usage:
  from trader.agent_verifier import verify_citations, VerificationResult
  result = verify_citations(agent_output, sample_size=2)
  if result.action == "abstain":
      raise ValueError(f"Agent output untrustworthy: {result.reasons}")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    TRUST = "trust"          # output passes — use directly
    VERIFY = "verify"        # mostly passes, but flag suspicious items for review
    ABSTAIN = "abstain"      # output untrustworthy — discard


@dataclass
class Citation:
    """Extracted citation from agent output."""
    arxiv_id: Optional[str] = None
    quoted_text: Optional[str] = None
    claimed_authors: Optional[str] = None
    raw_text: str = ""


@dataclass
class VerificationResult:
    action: Action
    confidence: float  # 0-1
    citations_found: list[Citation] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# Regex for arxiv IDs (NEW format YYMM.XXXXX, allowing 4 or 5 trailing digits)
ARXIV_PATTERN = re.compile(r"\b(\d{4}\.\d{4,5})\b")

# Suspicious author flags
ANON_AUTHORS = re.compile(r"\b(?:anonymous|anon\.?|undisclosed|withheld)\b", re.IGNORECASE)

# Specific red-flag phrases that signal LLM-trading paper anti-patterns
TRADING_PAPER_RED_FLAGS = [
    (r"sharpe (?:ratio of )?[2-9]\.\d+", "claims Sharpe ≥ 2.0 — high prior of methodological error"),
    (r"sharpe (?:ratio of )?[1-9]\d+(?:\.\d+)?", "claims Sharpe > 10 — almost certainly fabricated/look-ahead"),
    (r"(?:cumulative|total)\s+return\s+of\s+\d{3,}%", "claims 100%+ return — verify backtest window"),
    (r"using\s+(?:gpt-?[34]|claude|llama)\s+on\s+(?:201[0-9]|202[0-3])", "LLM-look-ahead: model trained AFTER backtest period"),
]


def extract_citations(text: str) -> list[Citation]:
    """Extract arxiv-style citations + claimed quotes from agent output."""
    citations = []
    for match in ARXIV_PATTERN.finditer(text):
        arxiv_id = match.group(1)
        # Try to extract a quoted passage near the arxiv ID
        # Look for text in quotes within ±500 chars of the citation
        start = max(0, match.start() - 500)
        end = min(len(text), match.end() + 500)
        context = text[start:end]
        quote_match = re.search(r'["\'](.{30,400}?)["\']', context)
        quoted = quote_match.group(1) if quote_match else None
        # Look for claimed authors — simple "by Name" or "Name et al" patterns
        author_match = re.search(
            r"(?:by\s+|\(\s*)([A-Z][a-zA-Z\-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][a-zA-Z\-]+))?)",
            context,
        )
        claimed_authors = author_match.group(1) if author_match else None
        citations.append(Citation(
            arxiv_id=arxiv_id,
            quoted_text=quoted,
            claimed_authors=claimed_authors,
            raw_text=context.strip()[:200],
        ))
    return citations


def detect_red_flags(text: str) -> list[str]:
    """Scan agent output for known anti-patterns."""
    flags = []
    if ANON_AUTHORS.search(text):
        flags.append(
            "ANONYMOUS AUTHOR: published arxiv papers require real authors. "
            "If agent claims a paper has 'Anonymous' authors, citation is likely fabricated."
        )
    for pattern, reason in TRADING_PAPER_RED_FLAGS:
        if re.search(pattern, text, re.IGNORECASE):
            flags.append(f"PATTERN: {reason}")
    # Also flag if agent CLAIMS to have web/arxiv access but is a sub-agent
    if re.search(r"(?:verified via|verified through|verified at)\s+arxiv", text, re.IGNORECASE):
        flags.append(
            "AGENT CLAIMS arxiv verification — sub-agents typically cannot fetch URLs. "
            "If agent claims to have verified, MUST independently verify."
        )
    return flags


def assess_verification_action(citations: list[Citation], red_flags: list[str]) -> tuple[Action, float, list[str]]:
    """Decide whether to trust, verify, or abstain on the agent output."""
    reasons = []

    # Hard abstain: anonymous authors on cited papers
    has_anon = any("ANONYMOUS AUTHOR" in f for f in red_flags)
    if has_anon:
        reasons.append("anonymous-author flag triggered")
        return Action.ABSTAIN, 0.0, reasons

    # Hard abstain: extreme Sharpe claims
    extreme_sharpe = any("> 10" in f for f in red_flags)
    if extreme_sharpe:
        reasons.append("extreme Sharpe claim — almost certainly fabricated")
        return Action.ABSTAIN, 0.0, reasons

    # Verify if any citations present
    if citations:
        reasons.append(f"{len(citations)} citation(s) extracted")
        # If ≥30% of citations have no quoted text, lower confidence
        with_quotes = sum(1 for c in citations if c.quoted_text)
        if with_quotes / len(citations) < 0.5:
            reasons.append("majority of citations lack verbatim quotes — verify manually")
            return Action.ABSTAIN, 0.3, reasons
        # If multiple red flags, downgrade to verify
        if len(red_flags) >= 2:
            reasons.append(f"{len(red_flags)} red flags — sample verification required")
            return Action.VERIFY, 0.5, reasons
        return Action.VERIFY, 0.75, reasons

    # No citations: trust if no flags, abstain if flags present
    if red_flags:
        reasons.append(f"no citations to verify but {len(red_flags)} red flags present")
        return Action.ABSTAIN, 0.2, reasons

    reasons.append("no citations + no flags = pure-reasoning output, trust")
    return Action.TRUST, 0.9, reasons


def verify_citations(agent_output: str) -> VerificationResult:
    """Main entry point. Run citation extraction + flag detection,
    return VerificationResult with action recommendation."""
    citations = extract_citations(agent_output)
    red_flags = detect_red_flags(agent_output)
    action, confidence, reasons = assess_verification_action(citations, red_flags)
    return VerificationResult(
        action=action,
        confidence=confidence,
        citations_found=citations,
        red_flags=red_flags,
        reasons=reasons,
    )


def sample_for_manual_check(result: VerificationResult, n: int = 2) -> list[str]:
    """Returns N arxiv URLs to manually verify via WebFetch."""
    if not result.citations_found:
        return []
    # Prefer citations with longest quoted text (most checkable)
    sorted_cites = sorted(
        result.citations_found,
        key=lambda c: -len(c.quoted_text or ""),
    )
    return [
        f"https://arxiv.org/abs/{c.arxiv_id}"
        for c in sorted_cites[:n]
        if c.arxiv_id
    ]
