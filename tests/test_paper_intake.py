"""Tests for scripts/paper_intake.py — arXiv triage rubric.

The fetch path is not tested here (network). The triage logic IS
tested, against constructed Paper objects covering each verdict path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from paper_intake import Paper, triage  # noqa: E402


def _mk(title: str, abstract: str, category: str = "q-fin.PM",
         categories: list[str] | None = None,
         authors: list[str] | None = None) -> Paper:
    return Paper(
        arxiv_id="0000.00000",
        title=title,
        authors=authors or ["Test Author"],
        abstract=abstract,
        primary_category=category,
        categories=categories or [category],
        summary_url="https://arxiv.org/abs/0000.00000",
    )


# ============================================================
# PASS verdicts (substrate fail or rigor fail)
# ============================================================
def test_nlp_paper_passes():
    """Pure NLP / CS paper should PASS on substrate fit."""
    p = _mk(
        title="LatentRAG: Efficient Retrieval-Augmented Generation",
        abstract=(
            "We propose a novel method for compressing the reasoning "
            "step of agentic RAG systems into latent space. Reduces "
            "inference latency by 90% on seven benchmarks."
        ),
        category="cs.CL",
        categories=["cs.CL", "cs.LG"],
    )
    r = triage(p)
    assert r.verdict == "PASS"
    # Substrate criterion should be the failing one
    assert r.criteria["1. Substrate fit (long-only US equity)"][0] is False


def test_options_paper_hard_disqualified():
    """Options-pricing paper is hard-stop even with rigor."""
    p = _mk(
        title="Option Pricing with Stochastic Volatility",
        abstract=(
            "We propose a novel option pricing model. Out-of-sample "
            "tests across multiple regimes and after transaction costs "
            "show consistent improvements over Black-Scholes."
        ),
        category="q-fin.MF",
    )
    r = triage(p)
    assert r.verdict == "PASS"
    assert r.criteria["1. Substrate fit (long-only US equity)"][0] is False


def test_crypto_paper_hard_disqualified():
    p = _mk(
        title="Bitcoin price prediction with deep learning",
        abstract="We forecast cryptocurrency returns out-of-sample.",
        category="q-fin.ST",
    )
    r = triage(p)
    assert r.verdict == "PASS"


def test_fixed_income_paper_hard_disqualified():
    p = _mk(
        title="Yield curve dynamics and fixed income returns",
        abstract="We study interest rates and yield curve regime shifts.",
        category="q-fin.PM",
    )
    r = triage(p)
    assert r.verdict == "PASS"


def test_weak_equity_paper_passes_or_shelves():
    """Equity paper with no OOS, cost, or regime mention should not PURSUE."""
    p = _mk(
        title="A new equity factor",
        abstract="We propose a new equity factor with strong in-sample alpha.",
    )
    r = triage(p)
    assert r.verdict in {"PASS", "SHELVE"}
    # Not PURSUE
    assert r.verdict != "PURSUE"


# ============================================================
# PURSUE verdicts (substrate + rigor + novelty all present)
# ============================================================
def test_rigorous_equity_factor_paper_pursues():
    """Paper hitting substrate + OOS + cost + regime + novelty should PURSUE."""
    p = _mk(
        title="A novel cross-sectional equity factor",
        abstract=(
            "We propose a new factor in the US equity cross-section. "
            "Walk-forward out-of-sample tests from 1990-2020 net of "
            "transaction costs (5 bps) show consistent alpha across "
            "subperiods, including the 2008 recession and post-COVID "
            "regime. The factor has low correlation with momentum."
        ),
    )
    r = triage(p)
    assert r.verdict == "PURSUE", \
        f"expected PURSUE, got {r.verdict}: {r.criteria}"
    assert r.score >= 5


def test_pursue_requires_all_three_rigor_axes_or_close():
    """A paper with OOS + cost but no regime should still PURSUE
    if substrate + novelty are clear."""
    p = _mk(
        title="A novel equity factor",
        abstract=(
            "We propose a new factor in the US equity cross-section. "
            "Out-of-sample tests net of transaction costs (5 bps/side) "
            "show consistent alpha. We introduce a novel mechanism."
        ),
    )
    r = triage(p)
    # OOS + cost + novelty = 3 axes + substrate = 4 → SHELVE
    # add regime → 5 → PURSUE. Here we have OOS, cost, novelty, substrate
    # but no regime; rigor axis = 2 (>=2 passes), so total = 5
    assert r.verdict == "PURSUE"


# ============================================================
# SHELVE verdicts (passes substrate, weak rigor)
# ============================================================
def test_equity_paper_with_oos_only_shelves():
    p = _mk(
        title="Equity factor model",
        abstract=(
            "We propose a stock-selection factor. Out-of-sample tests "
            "look promising."
        ),
    )
    r = triage(p)
    # Substrate ✅ + OOS ✅ + cost ❌ + regime ❌ + novelty ✅ + rigor (1/3) ❌
    # = 3 → SHELVE
    assert r.verdict == "SHELVE", \
        f"expected SHELVE, got {r.verdict}: {r.criteria}"


# ============================================================
# Edge cases — novelty detection
# ============================================================
def test_momentum_rebrand_flagged():
    p = _mk(
        title="Momentum strategies revisited",
        abstract=(
            "We revisit the value premium and momentum anomaly. "
            "Out-of-sample tests across subperiods net of transaction "
            "costs confirm the original Jegadeesh-Titman results."
        ),
    )
    r = triage(p)
    # Substrate (portfolio not mentioned but momentum implies stocks) +
    # OOS + cost + regime + no novelty signal but has decayed factor
    # The novelty axis should flag this as "incremental".
    novelty_passed, novelty_reason = r.criteria[
        "5. Novel mechanism (not a known-decayed factor rebrand)"
    ]
    assert "momentum" in novelty_reason.lower() or "incremental" in novelty_reason.lower()


def test_arxiv_id_parser():
    from paper_intake import _parse_arxiv_id
    assert _parse_arxiv_id("2605.06285") == "2605.06285"
    assert _parse_arxiv_id("https://arxiv.org/abs/2605.06285") == "2605.06285"
    assert _parse_arxiv_id("https://arxiv.org/abs/2605.06285v2") == "2605.06285"
    assert _parse_arxiv_id("arxiv.org/pdf/2605.06285.pdf") == "2605.06285"
