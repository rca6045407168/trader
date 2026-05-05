"""Tests for v3.72.2 — docker-compose healthcheck must use a binary
that's actually installed in Dockerfile.dashboard.

The bug: healthcheck called `wget` but the image only installs
`curl`. 4593 consecutive failures across 38h showed "unhealthy"
while the container ran fine. This test pins the binary used by
the healthcheck against the binaries actually installed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


_REPO = Path(__file__).resolve().parent.parent


def _compose_text() -> str:
    return (_REPO / "docker-compose.yml").read_text()


def _dockerfile_text() -> str:
    """The dashboard Dockerfile (Dockerfile.dashboard if present, else
    plain Dockerfile as fallback)."""
    p = _REPO / "Dockerfile.dashboard"
    if not p.exists():
        p = _REPO / "Dockerfile"
    return p.read_text()


def _healthcheck_command() -> str:
    """Extract the `test:` line from the dashboard service's
    healthcheck. Returns the raw string."""
    text = _compose_text()
    # Find the healthcheck block under `dashboard:` (the only service
    # that has one in this compose file)
    m = re.search(
        r"dashboard:.*?healthcheck:\s*\n\s*(?:#[^\n]*\n\s*)*"
        r"test:\s*\[(.*?)\]",
        text, re.DOTALL,
    )
    assert m is not None, "couldn't find dashboard healthcheck.test"
    return m.group(1)


def test_healthcheck_does_not_use_wget():
    """The original bug: wget was the binary. Don't regress."""
    cmd = _healthcheck_command()
    # `wget` may legitimately appear inside a URL-like string, but
    # not as a command invocation
    assert " wget " not in cmd, f"healthcheck still calls wget: {cmd}"
    # Looking at compose syntax: `wget -...` would have wget at start
    assert not cmd.lstrip().lstrip('"').startswith("wget"), \
        f"healthcheck command starts with wget: {cmd}"


def test_healthcheck_binary_is_installed_in_image():
    """The binary the healthcheck calls must be installed by the
    Dockerfile. Otherwise it fires 4593 false negatives."""
    cmd = _healthcheck_command()
    dockerfile = _dockerfile_text()
    # Look for common HTTP-probe binaries
    candidates = ("curl", "wget", "python", "python3")
    binary_found = None
    for b in candidates:
        if b in cmd:
            binary_found = b
            break
    assert binary_found is not None, (
        f"healthcheck doesn't use any known HTTP-probe binary: {cmd}")
    # If python(3): always available since the base is python:3.11
    if binary_found in ("python", "python3"):
        return
    # Otherwise the binary must be apt-installed in the Dockerfile
    assert binary_found in dockerfile, (
        f"healthcheck calls `{binary_found}` but Dockerfile doesn't "
        f"install it. This is exactly the v3.72.2 bug.")


def test_healthcheck_targets_streamlit_health_endpoint():
    """Sanity: the URL probed should be Streamlit's standard health
    endpoint, not a random path."""
    cmd = _healthcheck_command()
    assert "_stcore/health" in cmd, (
        "healthcheck should hit /_stcore/health (Streamlit's standard "
        f"health endpoint). got: {cmd}")


def test_dashboard_version_v3_72_2():
    text = (_REPO / "scripts" / "dashboard.py").read_text()
    assert "v3.72.2" in text
    assert 'st.caption("v3.72.2' in text
