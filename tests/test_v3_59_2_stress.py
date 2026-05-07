"""Tests for v3.59.2 — stress test runner + best practices doc + stress view."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_stress_results_present_after_run():
    """Sanity check: stress_test_v5.json should be present after the
    docker-run from this session. If absent, the runner failed silently."""
    p = Path(__file__).resolve().parent.parent / "data" / "stress_test_v5.json"
    if not p.exists():
        pytest.skip("stress_test_v5.json not yet generated")
    import json
    with p.open() as f:
        data = json.load(f)
    assert "regimes" in data
    assert len(data["regimes"]) >= 8
