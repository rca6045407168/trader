"""Tests for v3.68.3 — earnings reactor in daemon (--watch) mode."""
from __future__ import annotations

import os
import plistlib
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def _plist() -> dict:
    p = (Path(__file__).resolve().parent.parent / "infra" / "launchd"
         / "com.trader.earnings-reactor.plist")
    with open(p, "rb") as f:
        return plistlib.load(f)


# ============================================================
# Plist now runs the reactor in daemon mode
# ============================================================
def test_plist_invokes_watch_mode():
    """The reactor must be launched with --watch (daemon, polls forever)
    and an explicit --watch-interval (so the cadence is auditable)."""
    d = _plist()
    cmd = d["ProgramArguments"][2]
    assert "--watch" in cmd
    assert "--watch-interval" in cmd
    # Python -u (unbuffered) so logs flush in real time
    assert "python -u" in cmd


def test_plist_keepalive_for_auto_restart():
    """KeepAlive=true → launchd respawns the daemon on crash."""
    d = _plist()
    assert d.get("KeepAlive") is True


def test_plist_has_throttle_interval():
    """ThrottleInterval prevents tight crash loops if the daemon has
    a bug. Without this, a startup-crash bug would respawn forever."""
    d = _plist()
    assert "ThrottleInterval" in d
    assert d["ThrottleInterval"] >= 60


def test_plist_no_longer_uses_calendar_or_interval_schedule():
    """Daemon mode supersedes both the per-day calendar fires and the
    sleep-resilience StartInterval. Both should be gone — leaving them
    on top of KeepAlive would just trigger redundant-fire confusion."""
    d = _plist()
    assert "StartInterval" not in d
    assert "StartCalendarInterval" not in d


# ============================================================
# CLI flags
# ============================================================
def test_cli_supports_watch_flag():
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    assert "--watch" in text
    assert "--watch-interval" in text
    # Watch flag dispatches to a loop function
    assert "_watch_loop" in text


def test_cli_watch_interval_defaults_to_300():
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    # Default 300 sec = 5 min, configurable via env
    assert 'default=int(os.getenv("REACTOR_WATCH_INTERVAL", "300"))' in text


def test_watch_loop_handles_signals():
    """Daemon must handle SIGTERM cleanly (launchd reload uses SIGTERM,
    and we must not kill in-flight Claude calls)."""
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    assert "_install_signal_handlers" in text
    assert "SIGTERM" in text
    # Floor the interval at 60s in case env override is too aggressive
    assert "max(60," in text


def test_watch_loop_per_iter_exception_handler():
    """Single-iter errors must not tear down the daemon — that would
    make a transient EDGAR 503 cause a launchd restart."""
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    # The loop body must catch top-level Exception
    loop_idx = text.index("def _watch_loop")
    next_def = text.index("\ndef ", loop_idx + 1)
    body = text[loop_idx:next_def]
    assert "except Exception" in body
    assert "while not _SHUTDOWN" in body


def test_watch_loop_emits_per_iter_summary():
    """Each iter must print a summary line so `tail -f` shows progress."""
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    loop_idx = text.index("def _watch_loop")
    next_def = text.index("\ndef ", loop_idx + 1)
    body = text[loop_idx:next_def]
    assert "iter " in body
    assert "new signals" in body
    assert "flush=True" in body  # explicit flush for line-buffered output


def test_dashboard_version_v3_68_3():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.68.3 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.68.3" in text
    import re
    assert re.search(r'st\.caption\("v3\.6\d\.\d', text), \
        "sidebar must show some v3.6x.y version label"


def test_automation_doc_describes_daemon_mode():
    """AUTOMATION.md must reflect the v3.68.3 architecture change."""
    p = Path(__file__).resolve().parent.parent / "docs" / "AUTOMATION.md"
    text = p.read_text()
    assert "daemon mode" in text.lower()
    # Should call out the latency improvement
    assert "5 min" in text or "5-min" in text or "5min" in text
    # Should explain KeepAlive
    assert "KeepAlive" in text
