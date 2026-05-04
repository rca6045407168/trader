"""Tests for v3.68.1 — launchd auto-fire of the earnings reactor.

The reactor itself was already idempotent in v3.68.0 (UNIQUE on
symbol+accession). This release wires it into macOS launchd so it
runs without manual `python scripts/earnings_reactor.py` invocations.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Plist file exists + is well-formed XML + claims the right Label
# ============================================================
def _plist_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "infra" / "launchd"
            / "com.trader.earnings-reactor.plist")


def test_launchd_plist_exists():
    assert _plist_path().exists()


def test_launchd_plist_is_valid_xml():
    """Catches the most common plist mistake: bad XML."""
    ET.parse(_plist_path())


def _plist_dict() -> dict:
    """Minimal plist parser for the test (we only check string + array
    + integer keys; full plist parsing would need plistlib which works
    fine but is heavier than we need here)."""
    import plistlib
    with open(_plist_path(), "rb") as f:
        return plistlib.load(f)


def test_launchd_plist_label_matches_filename():
    """Apple's convention: Label == filename minus .plist."""
    d = _plist_dict()
    assert d["Label"] == "com.trader.earnings-reactor"


def test_launchd_plist_invokes_reactor_script():
    """The ProgramArguments must end with the earnings_reactor.py path."""
    d = _plist_dict()
    args = d["ProgramArguments"]
    # bash -c "<long command>"
    assert args[0] == "/bin/bash"
    assert args[1] == "-c"
    cmd = args[2]
    assert "scripts/earnings_reactor.py" in cmd
    # Activates the venv
    assert ".venv/bin/python" in cmd
    # Sources .env so ANTHROPIC_API_KEY is available
    assert "source .env" in cmd or "set -a" in cmd


def test_launchd_plist_has_calendar_schedule():
    """Weekdays only at 17:05 ET (post market close)."""
    d = _plist_dict()
    cal = d["StartCalendarInterval"]
    assert isinstance(cal, list)
    assert len(cal) == 5  # Mon-Fri
    weekdays = sorted(entry["Weekday"] for entry in cal)
    assert weekdays == [1, 2, 3, 4, 5]
    for entry in cal:
        assert entry["Hour"] == 17
        assert entry["Minute"] == 5


def test_launchd_plist_sleep_resilient():
    """Per the FlexHaul memory rule: pair StartCalendarInterval with
    RunAtLoad + StartInterval to catch fires missed during sleep.
    Without this, the calendar interval silently skips every fire that
    landed during a closed-laptop window."""
    d = _plist_dict()
    assert d["RunAtLoad"] is True
    # StartInterval must be set + reasonable (1h–24h band)
    assert "StartInterval" in d
    assert 3600 <= d["StartInterval"] <= 86400


def test_launchd_plist_logs_to_user_logs():
    """Per the OpenClaw safety + privacy rule: log to ~/Library/Logs,
    not /tmp (which is world-readable on shared hosts)."""
    d = _plist_dict()
    out = d["StandardOutPath"]
    err = d["StandardErrorPath"]
    assert "/Library/Logs/" in out
    assert "/Library/Logs/" in err
    # Both contain the job label so multi-job logs don't collide
    assert "trader-earnings-reactor" in out
    assert "trader-earnings-reactor" in err
    assert "/tmp/" not in out
    assert "/tmp/" not in err


# ============================================================
# Install script
# ============================================================
def _install_script_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "scripts"
            / "install_launchd_earnings.sh")


def test_install_script_exists_and_executable():
    p = _install_script_path()
    assert p.exists()
    mode = p.stat().st_mode
    # Owner-executable
    assert mode & stat.S_IXUSR


def test_install_script_supports_uninstall_flag():
    text = _install_script_path().read_text()
    assert "--uninstall" in text
    # Must call launchctl unload + remove the dst plist
    assert "launchctl unload" in text


def test_install_script_loads_via_launchctl():
    text = _install_script_path().read_text()
    assert "launchctl load" in text


def test_install_script_validates_venv_before_load():
    """If .venv/bin/python is missing, the install must FAIL fast,
    not silently install a job that will crash on every fire."""
    text = _install_script_path().read_text()
    assert ".venv/bin/python" in text
    assert "exit 1" in text


def test_install_script_idempotent():
    """Re-running the install must unload + reload, not stack two
    instances of the job."""
    text = _install_script_path().read_text()
    # The reload pattern: check if loaded → unload → load
    assert ("already loaded" in text.lower()
            or "launchctl unload" in text)


# ============================================================
# Docs
# ============================================================
def test_automation_doc_exists():
    p = (Path(__file__).resolve().parent.parent / "docs"
         / "AUTOMATION.md")
    assert p.exists()
    text = p.read_text()
    # Must explain the 3 layers (prewarm + launchd + orchestrator)
    for layer in ("prewarm", "launchd", "orchestrator"):
        assert layer in text.lower()
    # Must document the install + uninstall commands
    assert "install_launchd_earnings.sh" in text
    assert "--uninstall" in text


def test_automation_doc_lists_idempotency_guarantees():
    """The doc must explicitly call out idempotency or a future user
    will worry about double-firing the reactor."""
    p = (Path(__file__).resolve().parent.parent / "docs"
         / "AUTOMATION.md")
    text = p.read_text().lower()
    assert "idempot" in text


def test_dashboard_version_v3_68_1():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "v3.68.1" in text
    assert 'st.caption("v3.68.1' in text
