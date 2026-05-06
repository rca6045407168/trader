"""Tests for v3.73.3 — Risk roadmap dashboard view + Block A status."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


def _dashboard_text() -> str:
    return (ROOT / "scripts" / "dashboard.py").read_text()


# ============================================================
# Source docs are present (otherwise the view links nowhere)
# ============================================================
def test_all_six_round2_docs_exist():
    """The view points at six docs; all six must exist on disk."""
    expected = [
        "ROUND_2_SYNTHESIS.md", "RISK_FRAMEWORK.md",
        "ADVERSARIAL_THREAT_MODEL.md", "TAIL_RISK_PLAYBOOK.md",
        "FUND_FAILURE_CASE_STUDIES.md", "INFORMATION_THEORY_ALPHA.md",
    ]
    for doc in expected:
        p = ROOT / "docs" / doc
        assert p.exists(), f"missing: docs/{doc}"
        # Sanity: not zero-length
        assert p.stat().st_size > 1000, f"docs/{doc} is suspiciously empty"


# ============================================================
# View structure
# ============================================================
def test_view_function_defined():
    text = _dashboard_text()
    assert "def view_risk_roadmap" in text


def test_view_invokes_status_resolver():
    """The status-resolver function is the load-bearing piece — it
    decides which Block A items show as shipped vs pending."""
    text = _dashboard_text()
    view_idx = text.index("def view_risk_roadmap")
    next_def = text.index("\ndef ", view_idx + 1)
    body = text[view_idx:next_def]
    assert "_resolve_roadmap_status_block_a" in body


def test_status_resolver_defined():
    text = _dashboard_text()
    assert "def _resolve_roadmap_status_block_a" in text


def test_resolver_covers_all_eight_block_a_items():
    """Block A in ROUND_2_SYNTHESIS has 8 numbered items. The
    resolver must return a row for each."""
    # Import via the module file path — can't import dashboard.py
    # (instantiates Streamlit), so parse + count
    text = _dashboard_text()
    fn_idx = text.index("def _resolve_roadmap_status_block_a")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    # Each item is appended via status.append({...})
    # Counting the ID prefixes ("1. ", "2. ", ..., "8. ")
    for n in range(1, 9):
        assert f"{n}. " in body, f"resolver missing item #{n}"


def test_resolver_marks_v3_73_2_drawdown_as_shipped():
    """Item #3 (four-threshold drawdown) must auto-flip to shipped
    when DRAWDOWN_ESCALATION_PCT exists in risk_manager.py — i.e.
    after v3.73.2 lands. This is the auto-resolution contract."""
    text = _dashboard_text()
    fn_idx = text.index("def _resolve_roadmap_status_block_a")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    # The resolver must check for the v3.73.2 artifact
    assert "DRAWDOWN_ESCALATION_PCT" in body
    # And should reference v3.73.2 by name in the status string
    assert "v3.73.2" in body


def test_resolver_marks_v3_73_0_heartbeat_as_shipped():
    """Item #6 (heartbeat) must auto-resolve via file presence."""
    text = _dashboard_text()
    fn_idx = text.index("def _resolve_roadmap_status_block_a")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    assert "check_daily_heartbeat.py" in body
    assert "v3.73.0" in body


def test_resolver_handles_missing_artifacts_gracefully():
    """Items 4 (GPD), 5 (MI screen), 7 (VRP config) are v5-specific
    and don't have artifacts yet. Resolver must mark them pending
    without crashing."""
    text = _dashboard_text()
    fn_idx = text.index("def _resolve_roadmap_status_block_a")
    next_def = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def]
    # v5-specific descriptions appear in the status field
    assert "v5-specific" in body


# ============================================================
# Nav + dispatch wiring
# ============================================================
def test_nav_includes_risk_roadmap():
    """Without the nav entry, the view is unreachable."""
    text = _dashboard_text()
    assert '("🛡️ Risk roadmap", "risk_roadmap")' in text


def test_dispatch_includes_risk_roadmap():
    text = _dashboard_text()
    assert '"risk_roadmap": view_risk_roadmap,' in text


# ============================================================
# Content quality — links to docs not just titles
# ============================================================
def test_view_links_to_each_doc():
    """The view must include a clickable link to each source doc.
    Just listing titles isn't enough — users need to navigate."""
    text = _dashboard_text()
    view_idx = text.index("def view_risk_roadmap")
    # Cover the helper too since links are rendered there
    end_idx = text.index("\ndef view_world_class", view_idx)
    body = text[view_idx:end_idx]
    for doc in ("ROUND_2_SYNTHESIS.md", "RISK_FRAMEWORK.md",
                 "ADVERSARIAL_THREAT_MODEL.md", "TAIL_RISK_PLAYBOOK.md",
                 "FUND_FAILURE_CASE_STUDIES.md",
                 "INFORMATION_THEORY_ALPHA.md"):
        assert doc in body, f"view doesn't reference {doc}"


def test_view_includes_honest_framing():
    """The synthesis doc has a "honest framing" section about the
    opportunity-cost calculation. The view must surface this so the
    user always sees the cost-benefit reframe alongside the work
    list."""
    text = _dashboard_text()
    view_idx = text.index("def view_risk_roadmap")
    end_idx = text.index("\ndef view_world_class", view_idx)
    body = text[view_idx:end_idx]
    assert "honest framing" in body.lower() or "Honest framing" in body
    # Must include the actual reframe — not just say "see the doc"
    assert "$10k" in body or "10k" in body
    assert "GTM" in body or "primary work" in body


# ============================================================
# Block A / B / C all surfaced
# ============================================================
def test_view_renders_all_three_blocks():
    """The synthesis doc has Block A (mandatory), B (recommended),
    C (post-LIVE). All three must surface — partial would be
    misleading about the full picture."""
    text = _dashboard_text()
    view_idx = text.index("def view_risk_roadmap")
    end_idx = text.index("\ndef view_world_class", view_idx)
    body = text[view_idx:end_idx]
    assert "Block A" in body
    assert "Block B" in body
    assert "Block C" in body


def test_dashboard_version_v3_73_3():
    text = _dashboard_text()
    # v3.73.3 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.73.3" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"


def test_dockerfile_copies_docs_into_image():
    """Without `COPY docs/` the Risk roadmap view's inline render
    fails inside the container even though it works on the host.
    This test catches the Dockerfile/docker-compose-vs-runtime drift
    that's specific to the v3.73.3 view."""
    text = (ROOT / "Dockerfile.dashboard").read_text()
    # Either form is fine; the load-bearing thing is that docs/ ends
    # up at /app/docs/ inside the container
    assert "COPY" in text and "docs/" in text


def test_view_renders_doc_inline_not_link():
    """The `[Open foo.md](../docs/foo.md)` pattern doesn't work in
    Streamlit (relative paths don't resolve to served files). The
    view must read + render doc content INLINE via st.markdown."""
    text = _dashboard_text()
    view_idx = text.index("def view_risk_roadmap")
    end_idx = text.index("\ndef view_world_class", view_idx)
    body = text[view_idx:end_idx]
    # Must read text from the file
    assert ".read_text()" in body
    # And render it as markdown
    # (st.markdown already appears for the title; check for the doc-text path)
    assert "st.markdown(text)" in body or "st.markdown(text," in body
