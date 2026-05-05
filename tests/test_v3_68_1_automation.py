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


def test_launchd_plist_starts_at_load():
    """RunAtLoad=true so the daemon (or one-shot, depending on version)
    starts immediately when launchd registers the job. Without this,
    there's no initial fire and the user has to wait until the next
    scheduled trigger."""
    d = _plist_dict()
    assert d["RunAtLoad"] is True


def test_launchd_plist_has_a_fire_strategy():
    """v3.68.1 used StartCalendarInterval + StartInterval (fire-then-exit
    every 4h). v3.68.3 uses KeepAlive (daemon, runs forever). Either is
    valid — but SOMETHING must keep it running, otherwise it's a one-shot
    that fires once at install and never again."""
    d = _plist_dict()
    has_daemon_keepalive = d.get("KeepAlive") is True
    has_calendar_schedule = "StartCalendarInterval" in d
    has_interval_schedule = "StartInterval" in d
    assert (has_daemon_keepalive or has_calendar_schedule
            or has_interval_schedule), \
        "plist must have KeepAlive (daemon) OR a Start*Interval (cron-style)"


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
    # v3.68.1 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.68.1" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"
