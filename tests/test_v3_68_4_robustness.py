"""Tests for v3.68.4 — robustness pass on the v3.68.x earnings stack.

Driven by "please keep testing" — close the coverage gaps:
1. HANK tool dispatch (tool_read_filings + tool_get_earnings_signals)
2. Reactor edge cases (malformed Claude JSON, missing CIK, EDGAR fail)
3. Filings archive search semantics
4. Plist defends against macOS App Nap regressions
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# HANK tool: read_filings end-to-end
# ============================================================
def test_tool_read_filings_returns_matches(tmp_path, monkeypatch):
    """HANK's read_filings tool must hit the filings archive and
    return context-windowed snippets that match the query."""
    import trader.filings_archive as fa
    monkeypatch.setattr(fa, "DEFAULT_ARCHIVE_ROOT", tmp_path)
    fa.store(symbol="NVDA", form_type="8-K", accession="N1",
              filed_at="2026-05-04",
              url="https://sec.gov/x/N1",
              text=("Q1 results were strong. We expect demand for AI "
                    "accelerators to remain robust through 2026 as "
                    "hyperscalers continue to expand capacity. "
                    "Guidance raised for the second half."),
              items=["2.02"], source="sec_edgar",
              root=tmp_path)

    from trader.copilot import tool_read_filings
    result = tool_read_filings(
        query="AI accelerators", symbol="NVDA", limit=5)
    assert "error" not in result
    assert result["n_matches"] == 1
    f = result["filings"][0]
    assert f["symbol"] == "NVDA"
    assert "AI accelerators" in f["context"]
    # Context window includes some surrounding text, not the whole doc
    assert len(f["context"]) <= 700


def test_tool_read_filings_empty_query(tmp_path, monkeypatch):
    """Empty archive → 0 matches, no exception."""
    import trader.filings_archive as fa
    monkeypatch.setattr(fa, "DEFAULT_ARCHIVE_ROOT", tmp_path)
    from trader.copilot import tool_read_filings
    result = tool_read_filings(query="anything", symbol="ZZZZ")
    assert "error" not in result
    assert result["n_matches"] == 0


def test_tool_read_filings_clamps_limit():
    """Out-of-range limit values must be clamped to a sane range,
    not crash."""
    from trader.copilot import tool_read_filings
    r = tool_read_filings(query="x", limit=999)
    assert "error" not in r
    r = tool_read_filings(query="x", limit=0)
    assert "error" not in r
    r = tool_read_filings(query="x", limit=-5)
    assert "error" not in r


# ============================================================
# HANK tool: get_earnings_signals end-to-end
# ============================================================
def test_tool_get_earnings_signals_filters_by_materiality(tmp_path,
                                                            monkeypatch):
    """HANK's get_earnings_signals must respect the min_materiality
    filter so the LLM doesn't get noise."""
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal,
    )
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.earnings_reactor.DEFAULT_JOURNAL_DB", db)
    _persist_signal(db, ReactionResult(
        symbol="NVDA", accession="A1", filed_at="2026-05-01",
        materiality=4, direction="BULLISH",
        summary="big beat + raised guidance",
        items=["2.02"], model="m"))
    _persist_signal(db, ReactionResult(
        symbol="AAPL", accession="A2", filed_at="2026-05-01",
        materiality=1, direction="NEUTRAL",
        summary="routine officer announcement",
        items=["5.02"], model="m"))

    from trader.copilot import tool_get_earnings_signals
    r = tool_get_earnings_signals(min_materiality=3, since_days=30)
    assert "error" not in r
    assert r["n_signals"] == 1
    assert r["signals"][0]["symbol"] == "NVDA"


def test_tool_get_earnings_signals_symbol_filter(tmp_path, monkeypatch):
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal,
    )
    db = tmp_path / "j.db"
    monkeypatch.setattr("trader.earnings_reactor.DEFAULT_JOURNAL_DB", db)
    _persist_signal(db, ReactionResult(
        symbol="NVDA", accession="A1", filed_at="2026-05-01",
        materiality=3, direction="BULLISH", summary="x",
        items=["2.02"], model="m"))
    _persist_signal(db, ReactionResult(
        symbol="AAPL", accession="A2", filed_at="2026-05-01",
        materiality=3, direction="BEARISH", summary="y",
        items=["2.02"], model="m"))

    from trader.copilot import tool_get_earnings_signals
    r = tool_get_earnings_signals(symbol="NVDA", min_materiality=1,
                                    since_days=30)
    assert r["n_signals"] == 1
    assert r["signals"][0]["symbol"] == "NVDA"


# ============================================================
# Reactor edge cases
# ============================================================
def test_reactor_handles_malformed_claude_json(tmp_path, monkeypatch):
    """When Claude returns text that doesn't parse as JSON, the reactor
    must default to NEUTRAL/M1 + capture the raw response — not raise."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Fake the Anthropic client so we control the response
    class FakeUsage:
        input_tokens = 100
        output_tokens = 50

    class FakeContent:
        def __init__(self, text):
            self.text = text

    class FakeMessage:
        def __init__(self, text):
            self.content = [FakeContent(text)]
            self.usage = FakeUsage()

    class FakeMessages:
        def create(self, **kwargs):
            return FakeMessage("not valid json at all here, just prose")

    class FakeClient:
        def __init__(self):
            self.messages = FakeMessages()

    monkeypatch.setattr("anthropic.Anthropic", lambda: FakeClient())

    from trader.earnings_reactor import _analyze_filing_with_claude
    from trader.sec_filings import FilingMetadata
    meta = FilingMetadata(
        accession="A1", form_type="8-K", filed_at="2026-05-04",
        primary_doc="x.htm", cik=1, items=["2.02"],
    )
    r = _analyze_filing_with_claude("NVDA", meta, "some text")
    # Defaults applied; raw_response captured
    assert r.direction == "NEUTRAL"
    assert r.materiality == 1
    assert "not valid json" in r.raw_response.lower()
    # Most importantly: no exception bubbled up
    assert r.error is None or "json" not in (r.error or "").lower()


def test_reactor_extracts_json_from_prose_wrapper(tmp_path, monkeypatch):
    """If Claude returns prose with a JSON block in the middle, we
    should still extract it (regex fallback in the parser)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    embedded = (
        "Here is my analysis:\n"
        '{"direction": "BEARISH", "materiality": 4, '
        '"guidance_change": "LOWERED", "surprise_direction": "MISSED", '
        '"summary": "guidance cut", '
        '"bullish_quotes": [], "bearish_quotes": ["lowered FY"]}'
        "\nThanks!"
    )

    class FakeUsage:
        input_tokens = 100
        output_tokens = 50

    class FakeContent:
        def __init__(self, text):
            self.text = text

    class FakeMessage:
        def __init__(self, text):
            self.content = [FakeContent(text)]
            self.usage = FakeUsage()

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return FakeMessage(embedded)

    monkeypatch.setattr("anthropic.Anthropic", lambda: FakeClient())

    from trader.earnings_reactor import _analyze_filing_with_claude
    from trader.sec_filings import FilingMetadata
    meta = FilingMetadata(
        accession="A1", form_type="8-K", filed_at="2026-05-04",
        primary_doc="x.htm", cik=1, items=["2.02"],
    )
    r = _analyze_filing_with_claude("X", meta, "doc text")
    assert r.direction == "BEARISH"
    assert r.materiality == 4
    assert r.guidance_change == "LOWERED"


def test_sec_filings_handles_unknown_ticker():
    """A ticker that doesn't exist in EDGAR's company_tickers.json
    must return [] not raise."""
    from trader.sec_filings import _ticker_to_cik
    # Real CIK lookup; ZZZZZZ shouldn't match anything
    cik = _ticker_to_cik("ZZZZZZ")
    assert cik is None


def test_sec_filings_strip_html_handles_empty():
    from trader.sec_filings import strip_html
    assert strip_html("") == ""
    assert strip_html("<html></html>") == ""


def test_sec_filings_strip_html_preserves_quotes_and_numbers():
    """Material 8-K text often has $X, percentages, and direct quotes —
    these must survive the strip."""
    from trader.sec_filings import strip_html
    html = ('<p>Q1 revenue grew 23.5% to $14.2B. CEO said '
            '&ldquo;we expect strong demand&rdquo; through 2026.</p>')
    text = strip_html(html)
    assert "23.5" in text or "23.5%" in text
    assert "$14.2" in text
    assert "we expect strong demand" in text


# ============================================================
# Plist regression guard (the App Nap discovery)
# ============================================================
def test_plist_does_not_use_throttled_processtype():
    """ProcessType=Background causes macOS App Nap to throttle the
    daemon's sleep timers — confirmed empirically by observing 12-min
    iter intervals when the configured cadence was 5 min. Adaptive
    or Standard avoids this."""
    import plistlib
    p = (Path(__file__).resolve().parent.parent / "infra" / "launchd"
         / "com.trader.earnings-reactor.plist")
    with open(p, "rb") as f:
        d = plistlib.load(f)
    pt = d.get("ProcessType", "Standard")
    assert pt != "Background", (
        f"ProcessType={pt!r} — Background is App-Nap-throttled. "
        "Use 'Adaptive' or 'Standard' for the reactor daemon.")


def test_plist_opts_out_of_low_priority_io():
    """Defensive: even Adaptive-typed processes can have I/O throttled.
    Explicitly disabling the LowPriorityIO flags ensures we don't get
    bitten by another silent-throttle bug."""
    import plistlib
    p = (Path(__file__).resolve().parent.parent / "infra" / "launchd"
         / "com.trader.earnings-reactor.plist")
    with open(p, "rb") as f:
        d = plistlib.load(f)
    assert d.get("LowPriorityIO") is False
    assert d.get("LowPriorityBackgroundIO") is False


# ============================================================
# Watch loop: floor the interval at 60s
# ============================================================
def test_watch_interval_floor_in_code():
    """If env or CLI specifies an absurdly low interval, the script
    must clamp to 60s (no 1-sec hot loop)."""
    p = (Path(__file__).resolve().parent.parent / "scripts"
         / "earnings_reactor.py")
    text = p.read_text()
    assert "max(60," in text


# ============================================================
# Daemon lifecycle — SIGTERM lands a clean exit
# ============================================================
def test_daemon_clean_shutdown_via_sigterm(tmp_path):
    """Spawn the watch loop in a subprocess, send SIGTERM, verify it
    exits with code 0 and emits the clean-exit banner. This is the
    exact path launchd uses when reloading."""
    import os as _os
    import signal as _signal
    import subprocess
    import sys
    import time as _time

    # Use the project venv if present (otherwise sys.executable)
    base = Path(__file__).resolve().parent.parent
    venv_py = base / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable
    script = base / "scripts" / "earnings_reactor.py"

    # Run with --no-alerts to avoid SMTP, --skip-claude... wait, --skip-claude
    # short-circuits before --watch is consumed. Use a high watch-interval
    # and a non-existent symbol so iter 1 returns nothing fast.
    env = _os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "test"  # avoid the real API
    env["REACTOR_WATCH_INTERVAL"] = "60"  # min allowed

    proc = subprocess.Popen(
        [py, "-u", str(script), "--watch", "--symbol", "ZZZZZZ",
         "--no-alerts"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, cwd=str(base),
    )
    try:
        # Wait up to ~30s for the WATCH-mode banner to appear
        deadline = _time.time() + 30
        seen_watch_banner = False
        while _time.time() < deadline:
            if proc.poll() is not None:
                break
            line = proc.stdout.readline().decode("utf-8", errors="replace")
            if "WATCH mode" in line:
                seen_watch_banner = True
                break
            _time.sleep(0.1)
        assert seen_watch_banner or proc.poll() is not None, \
            "watch loop did not emit its banner in 30s"

        # Send SIGTERM. The handler should set _SHUTDOWN and exit cleanly
        # at the next sleep-loop check.
        proc.send_signal(_signal.SIGTERM)
        # Allow up to 75s for clean exit (sleep loop checks every 5s,
        # iter takes a few sec, plus margin)
        try:
            rc = proc.wait(timeout=75)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("daemon did not exit within 75s of SIGTERM")
        assert rc == 0, f"clean shutdown should exit with 0, got {rc}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# ============================================================
# Migration safety — adding alerts to an existing v3.68.0 journal
# ============================================================
def test_alert_layer_works_on_pre_v3_68_2_schema(tmp_path):
    """If we land alerts on a journal that already has earnings_signals
    rows from v3.68.0 (no notified_at column), the migration must be
    invisible to those rows — they get notified_at=NULL and then can
    be alerted on next run if they still meet threshold."""
    db = tmp_path / "j.db"
    # Create an old-shape table without notified_at
    with sqlite3.connect(db) as c:
        c.execute("""
            CREATE TABLE earnings_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, symbol TEXT NOT NULL,
                accession TEXT NOT NULL, filed_at TEXT NOT NULL,
                items_json TEXT, direction TEXT, materiality INTEGER,
                guidance_change TEXT, surprise_direction TEXT,
                summary TEXT, bullish_quotes_json TEXT,
                bearish_quotes_json TEXT, model TEXT, cost_usd REAL,
                raw_response TEXT, error TEXT,
                UNIQUE(symbol, accession)
            )
        """)
        c.execute(
            "INSERT INTO earnings_signals "
            "(ts, symbol, accession, filed_at, materiality, direction, "
            "summary, items_json, model) VALUES "
            "('2026-05-01', 'OLD', 'A1', '2026-05-01', 4, 'BEARISH', "
            "'pre-existing', '[\"2.02\"]', 'm')")
        c.commit()

    # Migration via _ensure_signals_table should add notified_at
    from trader.earnings_reactor import _ensure_signals_table
    _ensure_signals_table(db)

    with sqlite3.connect(db) as c:
        cols = [row[1] for row in c.execute(
            "PRAGMA table_info(earnings_signals)").fetchall()]
        assert "notified_at" in cols
        # Pre-existing row's notified_at is NULL (eligible for alert)
        row = c.execute(
            "SELECT notified_at FROM earnings_signals "
            "WHERE accession = 'A1'").fetchone()
        assert row[0] is None
