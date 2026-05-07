"""Tests for v3.68.0 — earnings reactor + filings archive.

Per the FT/LLMQuant article (2026-05-04): AI compresses
event-doc → structured-thesis time. We close the gap by:
1. Persistent on-disk archive of SEC filings (filings_archive.py)
2. Free EDGAR fetcher (sec_filings.py)
3. Reactor that orchestrates archive + Claude analysis
   (earnings_reactor.py)
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# filings_archive — pure-python (no network, no LLM)
# ============================================================
def test_filings_archive_module_imports():
    from trader.filings_archive import (
        Filing, init_db, store, exists, get, read_text,
        list_for_symbol, list_recent, search, stats,
    )
    assert callable(store)


def test_init_db_idempotent(tmp_path):
    from trader.filings_archive import init_db
    init_db(tmp_path)
    init_db(tmp_path)  # second call is a no-op
    db = tmp_path / "index.db"
    assert db.exists()
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert ("filings",) in rows


def test_store_and_get_round_trip(tmp_path):
    from trader.filings_archive import store, get, read_text, exists
    f = store(
        symbol="NVDA", form_type="8-K",
        accession="0001045810-26-000123",
        filed_at="2026-05-04", url="https://sec.gov/x",
        text="Strong Q1 results. Guidance raised.",
        items=["2.02", "9.01"], source="sec_edgar",
        title="Q1 2026 results",
        root=tmp_path,
    )
    assert f.symbol == "NVDA"
    assert f.n_chars == len("Strong Q1 results. Guidance raised.")
    assert exists("0001045810-26-000123", root=tmp_path)
    got = get("0001045810-26-000123", root=tmp_path)
    assert got is not None
    assert got.symbol == "NVDA"
    assert got.items == ["2.02", "9.01"]
    text = read_text("0001045810-26-000123", root=tmp_path)
    assert text == "Strong Q1 results. Guidance raised."


def test_store_is_idempotent_on_accession(tmp_path):
    """Re-storing the same accession overwrites the body but keeps
    the same key."""
    from trader.filings_archive import store, list_for_symbol
    store(symbol="AAPL", form_type="8-K", accession="A-1",
           filed_at="2026-05-01", url="u", text="v1",
           items=[], root=tmp_path)
    store(symbol="AAPL", form_type="8-K", accession="A-1",
           filed_at="2026-05-01", url="u", text="v2 corrected",
           items=[], root=tmp_path)
    rows = list_for_symbol("AAPL", root=tmp_path)
    assert len(rows) == 1


def test_list_for_symbol_filters_by_form_and_date(tmp_path):
    from trader.filings_archive import store, list_for_symbol
    store(symbol="AAPL", form_type="8-K", accession="A1",
           filed_at="2026-04-01", url="u", text="early",
           root=tmp_path)
    store(symbol="AAPL", form_type="8-K", accession="A2",
           filed_at="2026-05-01", url="u", text="recent",
           root=tmp_path)
    store(symbol="AAPL", form_type="10-Q", accession="A3",
           filed_at="2026-05-01", url="u", text="quarterly",
           root=tmp_path)
    # Newest first
    all_rows = list_for_symbol("AAPL", root=tmp_path)
    assert [r.accession for r in all_rows][0] in ("A2", "A3")
    # form_types filter
    only_8k = list_for_symbol("AAPL", form_types=["8-K"], root=tmp_path)
    assert {r.accession for r in only_8k} == {"A1", "A2"}
    # since filter
    since_apr15 = list_for_symbol("AAPL", since="2026-04-15", root=tmp_path)
    assert {r.accession for r in since_apr15} == {"A2", "A3"}


def test_search_returns_matches(tmp_path):
    from trader.filings_archive import store, search
    store(symbol="NVDA", form_type="8-K", accession="N1",
           filed_at="2026-05-01", url="u",
           text="we expect strong demand for AI accelerators",
           root=tmp_path)
    store(symbol="AMD", form_type="8-K", accession="M1",
           filed_at="2026-05-01", url="u",
           text="data center revenue grew 30%",
           root=tmp_path)
    hits = search("AI accelerators", root=tmp_path)
    assert len(hits) == 1
    assert hits[0].symbol == "NVDA"
    # Symbol-scoped
    none_for_amd = search("accelerators", symbol="AMD", root=tmp_path)
    assert none_for_amd == []


def test_stats_returns_counts(tmp_path):
    from trader.filings_archive import store, stats
    store(symbol="X", form_type="8-K", accession="X1",
           filed_at="2026-05-01", url="u", text="hello",
           root=tmp_path)
    store(symbol="Y", form_type="10-Q", accession="Y1",
           filed_at="2026-05-02", url="u", text="hi",
           root=tmp_path)
    s = stats(root=tmp_path)
    assert s["n_total"] == 2
    assert s["n_symbols"] == 2
    assert s["by_form"]["8-K"] == 1
    assert s["by_form"]["10-Q"] == 1


# ============================================================
# sec_filings — module imports + helpers (network-free tests)
# ============================================================
def test_sec_filings_module_imports():
    from trader.sec_filings import (
        FilingMetadata, fetch_recent_filings, download_filing,
        strip_html, is_earnings_8k, is_material_8k, fetch_and_pack,
    )
    assert callable(fetch_recent_filings)


def test_strip_html_basic():
    from trader.sec_filings import strip_html
    html = ("<html><body><h1>Title</h1><p>Para 1.</p>"
            "<script>evil()</script><p>Para 2.</p></body></html>")
    out = strip_html(html)
    assert "Title" in out
    assert "Para 1." in out
    assert "Para 2." in out
    assert "evil()" not in out


def test_strip_html_decodes_entities():
    from trader.sec_filings import strip_html
    out = strip_html("Q1 &amp; Q2 results &#8217;26")
    assert "&amp;" not in out
    assert "&#8217;" not in out
    assert "&" in out  # decoded


def test_is_earnings_8k():
    from trader.sec_filings import FilingMetadata, is_earnings_8k
    earnings = FilingMetadata(
        accession="A1", form_type="8-K", filed_at="2026-05-01",
        primary_doc="ex.htm", cik=1, items=["2.02", "9.01"],
    )
    not_earnings = FilingMetadata(
        accession="A2", form_type="8-K", filed_at="2026-05-01",
        primary_doc="ex.htm", cik=1, items=["5.02"],
    )
    other_form = FilingMetadata(
        accession="A3", form_type="10-Q", filed_at="2026-05-01",
        primary_doc="ex.htm", cik=1, items=["2.02"],
    )
    assert is_earnings_8k(earnings) is True
    assert is_earnings_8k(not_earnings) is False
    assert is_earnings_8k(other_form) is False


def test_is_material_8k():
    from trader.sec_filings import FilingMetadata, is_material_8k
    # Item 5.02 (officer change) is in the material set
    officer_change = FilingMetadata(
        accession="A1", form_type="8-K", filed_at="2026-05-01",
        primary_doc="ex.htm", cik=1, items=["5.02"],
    )
    # Item 9.01 (financial statements) alone is NOT material
    fs_only = FilingMetadata(
        accession="A2", form_type="8-K", filed_at="2026-05-01",
        primary_doc="ex.htm", cik=1, items=["9.01"],
    )
    assert is_material_8k(officer_change) is True
    assert is_material_8k(fs_only) is False


def test_filing_archive_url_format():
    from trader.sec_filings import FilingMetadata
    m = FilingMetadata(
        accession="0001045810-26-000123",
        form_type="8-K", filed_at="2026-05-04",
        primary_doc="nvda20260504.htm", cik=1045810, items=["2.02"],
    )
    url = m.archive_url
    assert "Archives/edgar/data/1045810" in url
    # Accession dashes stripped in path
    assert "000104581026000123" in url


# ============================================================
# earnings_reactor — DB schema + idempotency
# ============================================================
def test_earnings_reactor_module_imports():
    from trader.earnings_reactor import (
        ReactionResult, react_for_symbol, react_for_positions,
        recent_signals,
    )
    assert callable(react_for_symbol)


def test_signals_table_created_on_first_use(tmp_path):
    from trader.earnings_reactor import _ensure_signals_table
    db = tmp_path / "j.db"
    _ensure_signals_table(db)
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert ("earnings_signals",) in rows


def test_signal_persist_round_trip(tmp_path):
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, recent_signals,
    )
    db = tmp_path / "j.db"
    r = ReactionResult(
        symbol="NVDA", accession="0001045810-26-000123",
        filed_at="2026-05-04", items=["2.02", "9.01"],
        direction="BULLISH", materiality=4,
        guidance_change="RAISED", surprise_direction="BEAT",
        summary="Beat consensus + raised guidance.",
        bullish_quotes=["Demand for AI accelerators remains strong"],
        bearish_quotes=[], model="claude-sonnet-4-6", cost_usd=0.0123,
    )
    _persist_signal(db, r)
    rows = recent_signals(journal_db=db, since_days=365)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["direction"] == "BULLISH"
    assert rows[0]["materiality"] == 4
    assert rows[0]["bullish_quotes"] == ["Demand for AI accelerators remains strong"]


def test_signal_unique_on_symbol_accession(tmp_path):
    """Re-persisting the same (symbol, accession) replaces the old row,
    not duplicates it. Keeps the reactor idempotent."""
    from trader.earnings_reactor import (
        ReactionResult, _persist_signal, recent_signals,
    )
    db = tmp_path / "j.db"
    base = dict(symbol="X", accession="A1", filed_at="2026-05-01",
                 model="claude-sonnet-4-6")
    _persist_signal(db, ReactionResult(**base, direction="BULLISH",
                                          materiality=3,
                                          summary="first pass"))
    _persist_signal(db, ReactionResult(**base, direction="NEUTRAL",
                                          materiality=2,
                                          summary="re-analyzed"))
    rows = recent_signals(journal_db=db, since_days=365)
    assert len(rows) == 1
    assert rows[0]["summary"] == "re-analyzed"


def test_reactor_stubs_when_no_api_key(tmp_path, monkeypatch):
    """If ANTHROPIC_API_KEY is unset, the analyze step returns a stub
    NEUTRAL result with the error field populated — never raises."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from trader.earnings_reactor import _analyze_filing_with_claude
    from trader.sec_filings import FilingMetadata
    meta = FilingMetadata(
        accession="A1", form_type="8-K", filed_at="2026-05-01",
        primary_doc="x.htm", cik=1, items=["2.02"],
    )
    r = _analyze_filing_with_claude("NVDA", meta, "some text")
    assert r.direction == "NEUTRAL"
    assert r.error is not None
    assert "API" in (r.error or "").upper()


# ============================================================
# Dashboard wiring
# ============================================================
def test_dashboard_version_v3_68_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.68.0 changelog must remain in file history; sidebar may be
    # bumped by later patches (v3.68.x).
    assert "v3.68.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"


def test_hank_has_read_filings_tool():
    p = Path(__file__).resolve().parent.parent / "src" / "trader" / "copilot.py"
    text = p.read_text()
    assert '"name": "read_filings"' in text
    assert '"name": "get_earnings_signals"' in text
    assert "tool_read_filings" in text
    assert "tool_get_earnings_signals" in text
    # Both registered as read_only tier
    assert '"read_filings": "read_only"' in text
    assert '"get_earnings_signals": "read_only"' in text


def test_prewarm_includes_earnings_archive_section():
    p = Path(__file__).resolve().parent.parent / "scripts" / "prewarm.py"
    text = p.read_text()
    assert "earnings archive" in text
    # Skip-Claude path on prewarm (no token spend on container restart)
    assert "--skip-claude" in text
    # Idempotent: marker file gates same-day re-runs
    assert ".last_earnings_archive_run" in text


def test_cli_script_exists():
    p = Path(__file__).resolve().parent.parent / "scripts" / "earnings_reactor.py"
    assert p.exists()
    text = p.read_text()
    # Required CLI flags
    assert "--skip-claude" in text
    assert "--symbol" in text
    assert "--since-days" in text
