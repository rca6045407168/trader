"""Tests for agent_verifier module."""
from trader.agent_verifier import (
    verify_citations, extract_citations, detect_red_flags, Action,
)


def test_extracts_arxiv_ids():
    text = "See paper 2502.16789 (AlphaAgent) and 2505.07078 (FINSABER)."
    cits = extract_citations(text)
    ids = sorted(c.arxiv_id for c in cits)
    assert ids == ["2502.16789", "2505.07078"]


def test_extracts_quoted_text():
    text = '''The paper 2505.07078 (Li et al.) contains: "previously reported LLM advantages deteriorate significantly under broader cross-section."'''
    cits = extract_citations(text)
    assert len(cits) == 1
    assert "deteriorate" in cits[0].quoted_text


def test_anonymous_author_triggers_abstain():
    text = '''Paper 2604.13260 by Anonymous claims FF5 alpha 2.03%/mo.'''
    result = verify_citations(text)
    assert result.action == Action.ABSTAIN
    assert any("anonymous" in r.lower() for r in result.reasons)


def test_extreme_sharpe_claim_triggers_abstain():
    text = "Paper 2403.12345 by Smith claims Sharpe ratio of 15.2 on small-cap momentum."
    result = verify_citations(text)
    assert result.action == Action.ABSTAIN


def test_normal_citations_trigger_verify():
    text = '''Paper 2502.16789 (Tang) shows: "consistently delivering significant alpha in CSI 500 and S&P 500 markets" with IR ~1.05.'''
    result = verify_citations(text)
    assert result.action == Action.VERIFY
    assert result.confidence >= 0.5


def test_no_citations_pure_reasoning_trusted():
    text = "I cannot verify arxiv access; here are pattern-recognition observations: LLM-trading papers genre-wide have look-ahead bias."
    result = verify_citations(text)
    assert result.action == Action.TRUST


def test_agent_claiming_arxiv_verification_flagged():
    text = '''All IDs verified via arxiv API on 2026-05-02. Paper 2403.12345 by Smith has Sharpe 1.2.'''
    result = verify_citations(text)
    flags = " ".join(result.red_flags)
    assert "claims arxiv verification" in flags.lower()


def test_sample_for_manual_check():
    text = '''Paper 2502.16789: "first quote here that is long enough to be checkable" by Tang.
              Paper 2505.07078: "second longer quote that demonstrates the abstract content" by Li.
              Paper 2304.07619: "short" by Lopez.'''
    from trader.agent_verifier import sample_for_manual_check
    result = verify_citations(text)
    sample = sample_for_manual_check(result, n=2)
    assert len(sample) == 2
    # Should pick the 2 with longest quotes (Tang + Li, not Lopez)
    assert "2304.07619" not in " ".join(sample)
