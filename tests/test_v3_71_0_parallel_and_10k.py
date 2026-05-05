"""Tests for v3.71.0 — parallel reactor + 10-Q/10-K archiving.

Two changes:
  1. react_for_positions runs symbols in parallel (bounded 5 workers
     for EDGAR; bounded 3 workers for Claude via threading.Semaphore).
  2. react_for_symbol now fetches and archives 10-Q + 10-K alongside
     8-K. Claude analysis still fires only on material 8-Ks; other
     forms are archive-only (cross-quarter diff is a future release).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


# ============================================================
# Module-level constants exist + bounded
# ============================================================
def test_parallelism_constants_present():
    from trader.earnings_reactor import (
        EDGAR_PARALLEL_WORKERS, CLAUDE_PARALLEL_WORKERS,
    )
    assert 1 <= EDGAR_PARALLEL_WORKERS <= 10
    assert 1 <= CLAUDE_PARALLEL_WORKERS <= 5
    # Claude must be tighter than EDGAR (Anthropic rate limit < SEC's)
    assert CLAUDE_PARALLEL_WORKERS <= EDGAR_PARALLEL_WORKERS


def test_claude_semaphore_initialized():
    from trader.earnings_reactor import _CLAUDE_SEMAPHORE, CLAUDE_PARALLEL_WORKERS
    # BoundedSemaphore exposes _value as the current available slots
    # (an internal but stable enough attribute for this assertion)
    assert _CLAUDE_SEMAPHORE._value == CLAUDE_PARALLEL_WORKERS  # type: ignore


# ============================================================
# react_for_positions parallelism
# ============================================================
def test_react_for_positions_runs_symbols_in_parallel(tmp_path, monkeypatch):
    """Replace react_for_symbol with a slow stub that sleeps 0.5s.
    Sequential would take 15 × 0.5s = 7.5s; parallel with 5 workers
    should take ~1.5-2s. Asserts the parallel path actually parallelizes."""
    import trader.earnings_reactor as er

    call_log: list[tuple[str, float]] = []
    log_lock = threading.Lock()

    def slow_react(sym, **kwargs):
        with log_lock:
            call_log.append((sym, time.time()))
        time.sleep(0.3)
        return []

    monkeypatch.setattr(er, "react_for_symbol", slow_react)

    symbols = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    t0 = time.time()
    er.react_for_positions(symbols, max_workers=5,
                            journal_db=tmp_path / "j.db",
                            archive_root=tmp_path)
    elapsed = time.time() - t0

    # Sequential would be ~3.0s (10 × 0.3s); parallel with 5 workers
    # should be ~0.6-1.5s. Assert under 2s with margin.
    assert elapsed < 2.0, (
        f"react_for_positions took {elapsed:.2f}s for 10 symbols at "
        f"max_workers=5; expected < 2s for true parallelism")
    # All 10 symbols got polled
    assert len(call_log) == 10


def test_react_for_positions_isolates_per_symbol_failures(tmp_path,
                                                            monkeypatch):
    """A bad symbol's exception must not poison the others."""
    import trader.earnings_reactor as er

    def react(sym, **kwargs):
        if sym == "BAD":
            raise RuntimeError("simulated symbol failure")
        return [er.ReactionResult(symbol=sym, accession="A1",
                                    filed_at="2026-05-04",
                                    materiality=1, direction="NEUTRAL",
                                    summary="ok", model="m")]

    monkeypatch.setattr(er, "react_for_symbol", react)
    out = er.react_for_positions(
        ["GOOD1", "BAD", "GOOD2"], max_workers=3,
        journal_db=tmp_path / "j.db", archive_root=tmp_path,
    )
    # Bad symbol got [] not exception
    assert out["BAD"] == []
    # Good symbols still got results
    assert len(out["GOOD1"]) == 1
    assert len(out["GOOD2"]) == 1


def test_react_for_positions_empty_input(tmp_path):
    """Edge case: zero symbols. Returns {}, not raises."""
    from trader.earnings_reactor import react_for_positions
    out = react_for_positions([], journal_db=tmp_path / "j.db",
                                archive_root=tmp_path)
    assert out == {}


def test_react_for_positions_respects_max_workers_floor(tmp_path,
                                                          monkeypatch):
    """max_workers=0 must NOT crash ThreadPoolExecutor — caller-error
    band but we floor at 1."""
    import trader.earnings_reactor as er
    monkeypatch.setattr(er, "react_for_symbol", lambda s, **k: [])
    out = er.react_for_positions(
        ["A"], max_workers=0,
        journal_db=tmp_path / "j.db", archive_root=tmp_path,
    )
    assert "A" in out


# ============================================================
# 10-Q / 10-K archiving (no Claude)
# ============================================================
def test_react_for_symbol_archives_10q_without_running_claude(
    tmp_path, monkeypatch,
):
    """A 10-Q in the EDGAR feed must land in the archive. Must NOT
    trigger _analyze_filing_with_claude — that's only for material
    8-Ks. Verifies the v3.71.0 split where 10-Q/10-K are
    archive-only."""
    import trader.earnings_reactor as er
    import trader.sec_filings as sf
    import trader.filings_archive as fa

    # Fake EDGAR responses
    fake_metas = [
        sf.FilingMetadata(
            accession="000NVDA-26-Q1", form_type="10-Q",
            filed_at="2026-04-30", primary_doc="nvda10q.htm",
            cik=1045810, items=[],
        ),
    ]
    monkeypatch.setattr(sf, "fetch_recent_filings",
                         lambda *args, **kwargs: fake_metas)
    monkeypatch.setattr(sf, "download_filing",
                         lambda meta: "<html>10-Q content here</html>")

    # If Claude analysis fires, this would raise — tests fail loud
    def boom(*args, **kwargs):
        raise AssertionError("Claude should not run on 10-Q")
    monkeypatch.setattr(er, "_analyze_filing_with_claude", boom)

    db = tmp_path / "journal.db"
    archive = tmp_path / "filings"
    results = er.react_for_symbol("NVDA", journal_db=db,
                                     archive_root=archive)
    # No signal rows
    assert results == []
    # But the 10-Q IS archived
    archived = fa.list_for_symbol("NVDA", form_types=["10-Q"],
                                     root=archive)
    assert len(archived) == 1
    assert archived[0].accession == "000NVDA-26-Q1"


def test_react_for_symbol_archives_10k_without_running_claude(
    tmp_path, monkeypatch,
):
    """Same but for 10-K (annual)."""
    import trader.earnings_reactor as er
    import trader.sec_filings as sf
    import trader.filings_archive as fa

    monkeypatch.setattr(sf, "fetch_recent_filings",
                         lambda *args, **kwargs: [
                             sf.FilingMetadata(
                                 accession="000AAPL-26-K", form_type="10-K",
                                 filed_at="2026-02-01",
                                 primary_doc="aapl10k.htm",
                                 cik=320193, items=[],
                             ),
                         ])
    monkeypatch.setattr(sf, "download_filing",
                         lambda meta: "10-K content")

    def boom(*args, **kwargs):
        raise AssertionError("Claude should not run on 10-K")
    monkeypatch.setattr(er, "_analyze_filing_with_claude", boom)

    archive = tmp_path / "filings"
    er.react_for_symbol("AAPL", journal_db=tmp_path / "j.db",
                          archive_root=archive)
    archived = fa.list_for_symbol("AAPL", form_types=["10-K"],
                                     root=archive)
    assert len(archived) == 1


def test_react_for_symbol_still_runs_claude_on_material_8k(
    tmp_path, monkeypatch,
):
    """Regression guard — the v3.71.0 form-type split must NOT have
    accidentally turned off Claude analysis for 8-Ks."""
    import trader.earnings_reactor as er
    import trader.sec_filings as sf

    metas = [
        sf.FilingMetadata(
            accession="0001-26-001", form_type="8-K",
            filed_at="2026-05-04", primary_doc="x.htm",
            cik=1, items=["2.02"],  # Item 2.02 = material earnings
        ),
    ]
    monkeypatch.setattr(sf, "fetch_recent_filings",
                         lambda *args, **kwargs: metas)
    monkeypatch.setattr(sf, "download_filing",
                         lambda meta: "earnings press release content")

    claude_calls: list[str] = []
    def fake_claude(symbol, meta, text, model=None):
        claude_calls.append(meta.accession)
        return er.ReactionResult(
            symbol=symbol, accession=meta.accession,
            filed_at=meta.filed_at, items=meta.items,
            direction="NEUTRAL", materiality=2,
            summary="x", model=model or "m",
        )
    monkeypatch.setattr(er, "_analyze_filing_with_claude", fake_claude)

    er.react_for_symbol("X", journal_db=tmp_path / "j.db",
                          archive_root=tmp_path)
    assert claude_calls == ["0001-26-001"]


def test_archive_idempotent_across_multiple_polls(tmp_path, monkeypatch):
    """Re-polling the same EDGAR feed (which the daemon does every
    iter) must NOT re-download or re-archive filings. The accession
    UNIQUE check is the gate."""
    import trader.earnings_reactor as er
    import trader.sec_filings as sf
    import trader.filings_archive as fa

    metas = [
        sf.FilingMetadata(
            accession="REPEAT-1", form_type="10-Q",
            filed_at="2026-04-30", primary_doc="x.htm",
            cik=1, items=[],
        ),
    ]
    monkeypatch.setattr(sf, "fetch_recent_filings",
                         lambda *args, **kwargs: metas)
    download_calls: list[str] = []
    def fake_dl(meta):
        download_calls.append(meta.accession)
        return "content"
    monkeypatch.setattr(sf, "download_filing", fake_dl)

    archive = tmp_path / "filings"
    db = tmp_path / "j.db"
    # First poll: download fires
    er.react_for_symbol("X", journal_db=db, archive_root=archive)
    assert download_calls == ["REPEAT-1"]
    # Second poll: archive already has it — download must NOT fire
    er.react_for_symbol("X", journal_db=db, archive_root=archive)
    assert download_calls == ["REPEAT-1"]  # unchanged
    # And archive still has exactly one row for that accession
    rows = fa.list_for_symbol("X", root=archive)
    assert len(rows) == 1


# ============================================================
# Helper: _archive_filing_if_new
# ============================================================
def test_archive_filing_if_new_returns_false_when_already_stored(
    tmp_path, monkeypatch,
):
    import trader.earnings_reactor as er
    import trader.sec_filings as sf
    import trader.filings_archive as fa

    meta = sf.FilingMetadata(
        accession="DUP-1", form_type="8-K",
        filed_at="2026-05-04", primary_doc="x.htm",
        cik=1, items=["2.02"],
    )
    monkeypatch.setattr(sf, "download_filing", lambda m: "content")

    archive = tmp_path / "filings"
    # First call archives + returns True
    assert er._archive_filing_if_new("X", meta, archive) is True
    # Second call sees existing accession, returns False
    assert er._archive_filing_if_new("X", meta, archive) is False


def test_archive_filing_if_new_handles_download_failure(tmp_path,
                                                          monkeypatch):
    """If download_filing returns None (network error), don't store
    a half-baked row — return False."""
    import trader.earnings_reactor as er
    import trader.sec_filings as sf

    meta = sf.FilingMetadata(
        accession="FAIL-1", form_type="10-Q",
        filed_at="2026-05-04", primary_doc="x.htm",
        cik=1, items=[],
    )
    monkeypatch.setattr(sf, "download_filing", lambda m: None)
    assert er._archive_filing_if_new("X", meta, tmp_path) is False


# ============================================================
# Version
# ============================================================
def test_dashboard_version_v3_71_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    assert "v3.71.0" in text
    assert 'st.caption("v3.71.0' in text
