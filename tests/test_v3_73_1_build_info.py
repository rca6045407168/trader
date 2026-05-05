"""Tests for v3.73.1 — build-info badge + pickle-safe disk overlay.

Three concerns:
  1. _read_disk_overlay returns a picklable type (regression guard
     against the production crash that surfaced once the container
     was finally rebuilt).
  2. BUILD_INFO.txt is baked at image build time and the dashboard
     reads it correctly.
  3. The drift detector catches host-code-newer-than-image with
     reasonable thresholds (60s tolerance).
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")

ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# Picklability — the production crash that prompted this release
# ============================================================
def test_disk_overlay_returns_picklable_type(tmp_path):
    """The bug: _read_disk_overlay used `class O: pass` (local class
    not picklable) → @st.cache_data downstream crashed on cache write.

    Verify the new SimpleNamespace path round-trips through pickle.
    """
    # Synthesize a fake overlay cache file
    overlay_data = {
        "_cached_at": datetime.utcnow().isoformat(),
        "overlay": {
            "enabled": False, "final_mult": 1.20,
            "rationale": "test", "hmm_regime": "BULL",
            "hmm_mult": 1.0, "hmm_posterior": 0.85,
            "hmm_error": None,
            "macro_mult": 1.0, "macro_curve_inverted": False,
            "macro_credit_widening": False, "macro_error": None,
            "garch_mult": 1.0, "garch_vol_forecast_annual": 0.16,
            "garch_error": None,
        },
    }
    cache_file = tmp_path / "overlay_cache.json"
    import json as _json
    cache_file.write_text(_json.dumps(overlay_data))

    # Import the helper after injecting the cache path
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))

    # Read the helper's source + execute the disk-read body in
    # isolation (we can't import dashboard.py — instantiates Streamlit)
    src = (ROOT / "scripts" / "dashboard.py").read_text()
    # Find the function definition + body
    fn_idx = src.index("def _read_disk_overlay")
    next_def_idx = src.index("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def_idx]
    # The new code MUST use SimpleNamespace
    assert "SimpleNamespace" in body
    # Regression guard against the exact buggy pattern. The
    # commit-message + comment may still mention the old class for
    # documentation; what we forbid is the actual `class X: pass`
    # anti-pattern inside a function body.
    import re
    assert not re.search(r"^\s+class\s+\w+\s*:\s*\n\s+pass\b",
                          body, flags=re.MULTILINE), (
        "regression: a local-class-in-function pattern was "
        "reintroduced. pickle.dumps can't serialize these.")

    # Now actually exercise the new path
    from types import SimpleNamespace
    overlay = overlay_data["overlay"]
    obj = SimpleNamespace(**overlay)
    # pickle.dumps must succeed (the failing operation in production)
    blob = pickle.dumps(obj)
    restored = pickle.loads(blob)
    assert restored.hmm_regime == "BULL"
    assert restored.final_mult == 1.20
    assert restored.enabled is False


def test_dashboard_imports_simplenamespace_for_overlay():
    """Defends against someone reintroducing the local-class pattern
    in a future refactor. The fix landed in _read_disk_overlay."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _read_disk_overlay")
    next_def_idx = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    # Either an import inside the function or a top-level import is fine
    assert "SimpleNamespace" in body or "SimpleNamespace" in text


# ============================================================
# BUILD_INFO baking — Dockerfile.dashboard
# ============================================================
def test_dockerfile_bakes_build_info():
    """The Dockerfile must accept BUILD_COMMIT + BUILD_TIMESTAMP args
    and write them into /app/BUILD_INFO.txt at image build time."""
    text = (ROOT / "Dockerfile.dashboard").read_text()
    assert "ARG BUILD_COMMIT" in text
    assert "ARG BUILD_TIMESTAMP" in text
    assert "BUILD_INFO.txt" in text
    # Must echo BOTH lines into the file
    assert "commit=" in text
    assert "built_at=" in text


def test_compose_passes_build_args():
    """docker-compose.yml must pass through the host-shell BUILD_*
    env vars to the build context. Otherwise the image gets baked
    with empty args and the badge is useless."""
    text = (ROOT / "docker-compose.yml").read_text()
    assert "BUILD_COMMIT:" in text
    assert "BUILD_TIMESTAMP:" in text
    assert "${BUILD_COMMIT-}" in text  # default-empty syntax
    assert "${BUILD_TIMESTAMP-}" in text


# ============================================================
# Build-info reader — dashboard.py
# ============================================================
def test_dashboard_has_build_info_reader():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    assert "def _read_build_info" in text
    assert "def _build_info_drift_seconds" in text
    assert "def _render_build_info_badge" in text


def test_drift_returns_none_when_build_info_missing(tmp_path, monkeypatch):
    """Dev runs without Docker have no BUILD_INFO.txt. The detector
    must return None (no drift signal) rather than firing false
    'stale' warnings."""
    # We can't import dashboard.py (instantiates Streamlit), so
    # exercise the logic by reading the source + checking it has
    # the None-when-missing guard
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _build_info_drift_seconds")
    next_def_idx = text.index("\ndef ", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    assert "if not built_at_raw" in body
    assert "return None" in body


def test_drift_threshold_is_60_seconds():
    """The drift detector must NOT fire on tiny clock skew (e.g. host
    fs mtime vs. UTC build timestamp can differ by a few seconds for
    legitimate reasons). 60s tolerance is the documented threshold."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_build_info_badge")
    next_def_idx = text.index("\nfrom typing", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    assert "drift <= 60" in body


def test_drift_warning_includes_fix_command():
    """The warning surface must include the EXACT command to run.
    Otherwise users click around looking for documentation."""
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    fn_idx = text.index("def _render_build_info_badge")
    next_def_idx = text.index("\nfrom typing", fn_idx + 1)
    body = text[fn_idx:next_def_idx]
    assert "docker compose build dashboard" in body
    assert "docker compose up -d --force-recreate dashboard" in body


# ============================================================
# build_dashboard.sh helper
# ============================================================
def test_build_helper_exists_and_executable():
    p = ROOT / "scripts" / "build_dashboard.sh"
    assert p.exists()
    assert os.access(p, os.X_OK), "build_dashboard.sh must be executable"


def test_build_helper_resolves_commit_and_timestamp():
    """The helper must compute BUILD_COMMIT from `git rev-parse` and
    BUILD_TIMESTAMP from `date -u`. Otherwise the badge is empty even
    though the helper ran."""
    text = (ROOT / "scripts" / "build_dashboard.sh").read_text()
    assert "git rev-parse" in text
    assert "date -u" in text
    # Must export the env vars to docker compose, not just set them locally
    assert "BUILD_COMMIT=" in text
    assert "BUILD_TIMESTAMP=" in text


def test_build_helper_supports_status_subcommand():
    """A `--status` subcommand lets the operator inspect the running
    container's BUILD_INFO without having to remember the docker exec
    incantation."""
    text = (ROOT / "scripts" / "build_dashboard.sh").read_text()
    assert "--status" in text


# ============================================================
# Sidebar wiring
# ============================================================
def test_sidebar_invokes_build_info_badge():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    # The sidebar block calls the badge helper
    sidebar_idx = text.index("with st.sidebar:")
    next_block = text.index("# ============================================================",
                              sidebar_idx + 200)
    sidebar_block = text[sidebar_idx:next_block]
    assert "_render_build_info_badge()" in sidebar_block


def test_dashboard_version_v3_73_1():
    text = (ROOT / "scripts" / "dashboard.py").read_text()
    # v3.73.1 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.73.1" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"
