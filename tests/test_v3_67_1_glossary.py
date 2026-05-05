"""Tests for v3.67.1 — glossary + nav rename for shadow/sleeve/
validation overlap. Resolves the "all these tabs sound like the same
thing" problem.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def test_glossary_doc_exists():
    p = Path(__file__).resolve().parent.parent / "docs" / "GLOSSARY.md"
    assert p.exists()
    text = p.read_text()
    # Must define each term that was previously ambiguous
    for term in ("Sleeve", "Strategy", "V5 sleeves", "Shadow signal",
                  "Shadow variant", "Validation", "Stress test",
                  "Sleeve health", "Refutation", "CALMAR_TRADE",
                  "Regime overlay"):
        assert term in text, f"GLOSSARY.md missing: {term}"


def test_glossary_distinguishes_shadow_signal_vs_variant():
    """The TWO ambiguous 'shadow' concepts must be explicitly contrasted."""
    p = Path(__file__).resolve().parent.parent / "docs" / "GLOSSARY.md"
    text = p.read_text()
    # Both definitions present
    assert "Shadow signal" in text
    assert "Shadow variant" in text
    # Explicit difference call-out
    assert "Difference:" in text


def test_glossary_distinguishes_validation_vs_stress():
    p = Path(__file__).resolve().parent.parent / "docs" / "GLOSSARY.md"
    text = p.read_text()
    # Validation = "is the edge real on average?"
    assert "edge is real" in text or "edge real" in text
    # Stress = "if 2008 / 2020 happened again?"
    assert "2008" in text
    assert "2020" in text


def test_nav_rename_qualifiers_present():
    """Each previously-ambiguous nav item must have a qualifier
    in parentheses or a renamed label."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Renamed labels in NAV_GROUPS
    assert "👁️ Shadow signals (live)" in text
    assert "🧪 A/B sleeve variants" in text
    assert "🧪 Validation (walk-forward)" in text
    assert "💥 Stress test (crisis)" in text
    assert "🩺 Sleeve health (correlation)" in text
    assert "🎯 V5 alpha sleeves" in text


def test_nav_split_into_research_shadow_diagnostics():
    """The old 12-item Research group split into 3 sub-groups."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Three new group headers
    assert '("🔬 Research"' in text
    assert '("👁️ Shadow track"' in text
    assert '("🩺 Diagnostics"' in text


def test_renamed_view_titles_match_nav_labels():
    """Renamed nav items must have matching view titles so the user
    isn't confused by clicking '🧪 A/B sleeve variants' and landing on
    a page titled '👥 Shadow variants'."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # Each renamed nav item also gets a renamed view title
    assert 'st.title("🧪 A/B sleeve variants")' in text
    assert 'st.title("🩺 Sleeve health (correlation)")' in text
    assert 'st.title("👁️ Shadow signals (live)")' in text
    assert 'st.title("🧪 Validation (walk-forward)")' in text
    assert 'st.title("💥 Stress test (crisis regimes)")' in text
    assert 'st.title("🎯 V5 alpha sleeves")' in text


def test_glossary_cross_referenced_from_views():
    """At least the 3 most-confusable views should link to the glossary."""
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "GLOSSARY.md" in text
    # The view captions should reference the glossary anchor for
    # disambiguation
    occurrences = text.count("GLOSSARY.md")
    assert occurrences >= 3, \
        f"expected glossary cross-refs from at least 3 views, got {occurrences}"


def test_dashboard_version_v3_67_1():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.67.1 changelog comment must remain even after later patches.
    # Sidebar caption may have moved to a later release.
    assert "v3.67.1" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"
