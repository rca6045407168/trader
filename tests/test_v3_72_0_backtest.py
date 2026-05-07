"""Tests for v3.72.0 — ReactorSignalRule backtest harness."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


def _seed_journal(db: Path, signals: list[dict],
                    rebalance_decisions: list[dict]):
    """Seed both the earnings_signals + decisions tables for backtest."""
    from trader.earnings_reactor import (
        _ensure_signals_table, _persist_signal, ReactionResult,
    )
    _ensure_signals_table(db)
    for s in signals:
        _persist_signal(db, ReactionResult(
            symbol=s["symbol"], accession=s["accession"],
            filed_at=s["filed_at"],
            materiality=s.get("materiality", 1),
            direction=s.get("direction", "NEUTRAL"),
            surprise_direction=s.get("surprise_direction", "NONE"),
            summary=s.get("summary", ""),
            items=s.get("items", []), model="m",
        ))
    # Seed decisions table (mimics the production rebalance writer)
    with sqlite3.connect(db) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, ticker TEXT NOT NULL,
                action TEXT NOT NULL, style TEXT,
                score REAL, rationale_json TEXT,
                bull TEXT, bear TEXT,
                risk_decision TEXT, final TEXT
            )
        """)
        for d in rebalance_decisions:
            final = (f"LIVE_VARIANT_BUY @ {d['weight_pct']:.1f}% "
                     f"(variant=test_v1)")
            c.execute(
                "INSERT INTO decisions "
                "(ts, ticker, action, style, score, final) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (d["ts"], d["ticker"], "BUY", "MOMENTUM", 0.5, final))
        c.commit()


# ============================================================
# Module structure
# ============================================================
def test_backtest_module_imports():
    from trader.reactor_backtest import (
        BacktestResult, TrimEvent, replay, parameter_sweep,
        TRIM_DIRECTIONS, TRIM_SURPRISE_DIRECTIONS,
    )
    assert "BEARISH" in TRIM_DIRECTIONS


# ============================================================
# replay() empty-data behavior
# ============================================================
def test_replay_with_no_journal_returns_safe_result(tmp_path):
    from trader.reactor_backtest import replay
    r = replay(journal_db=tmp_path / "missing.db",
                pull_forward_prices=False)
    assert r.n_rebalances_analyzed == 0
    assert r.n_trims_triggered == 0
    assert "no rebalances" in r.summary().lower()


def test_replay_with_no_signals_returns_zero_trims(tmp_path):
    """Journal has rebalances but no signals — backtest reports
    'no trims, scanned N rebalances, 0 signals'."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[], rebalance_decisions=[
        {"ts": "2026-04-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
        {"ts": "2026-04-01T22:00:00", "ticker": "AAPL", "weight_pct": 5.0},
    ])
    r = replay(journal_db=db, pull_forward_prices=False)
    assert r.n_rebalances_analyzed == 1
    assert r.n_trims_triggered == 0
    assert r.n_signals_in_window == 0


# ============================================================
# Trigger logic — direction × materiality × held-by-portfolio
# ============================================================
def test_replay_triggers_on_bearish_above_threshold(tmp_path):
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 4, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA",
         "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=4, trim_pct=0.5,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 1
    e = r.trim_events[0]
    assert e.symbol == "NVDA"
    assert abs(e.original_target_weight - 0.08) < 1e-9
    assert abs(e.counterfactual_target_weight - 0.04) < 1e-9


def test_replay_skips_below_threshold(tmp_path):
    """M3 signal must NOT trigger when threshold is M≥4."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 3, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA",
         "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=4,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 0
    # But still counted in n_signals_in_window
    assert r.n_signals_in_window == 1


def test_replay_threshold_lowering_picks_up_trim(tmp_path):
    """Same signal as above; lowering to M≥3 triggers."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 3, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA",
         "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=3,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 1


def test_replay_skips_bullish_signals(tmp_path):
    """Critical: BULLISH M5 must NOT trigger a trim — boost
    decisions stay with humans per the article's pattern."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 5, "direction": "BULLISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA",
         "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=3,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 0


def test_replay_surprise_only_triggers_on_missed(tmp_path):
    """SURPRISE/BEAT shouldn't trigger; SURPRISE/MISSED should."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 5, "direction": "SURPRISE",
         "surprise_direction": "BEAT"},
        {"symbol": "AAPL", "accession": "A2",
         "filed_at": "2026-04-30",
         "materiality": 4, "direction": "SURPRISE",
         "surprise_direction": "MISSED"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
        {"ts": "2026-05-01T22:00:00", "ticker": "AAPL", "weight_pct": 6.0},
    ])
    r = replay(journal_db=db, min_materiality=3,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 1
    assert r.trim_events[0].symbol == "AAPL"


def test_replay_skips_signals_for_unheld_symbols(tmp_path):
    """Reactor flagged FOO but FOO isn't in the rebalance — no trim."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "FOO", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 5, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=3,
                pull_forward_prices=False)
    # FOO not held → no trim
    assert r.n_trims_triggered == 0


def test_replay_signals_with_error_skipped(tmp_path):
    """Signals with non-empty error field can't be trusted."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 5, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    # Patch the row to add an error
    with sqlite3.connect(db) as c:
        c.execute("UPDATE earnings_signals "
                  "SET error = 'simulated parse failure' "
                  "WHERE accession = ?", ("A1",))
        c.commit()
    r = replay(journal_db=db, min_materiality=3,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 0


def test_replay_lookback_window_excludes_old_signals(tmp_path):
    """Signal filed 30 days before rebalance shouldn't trigger when
    lookback=14."""
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-01",   # 30 days before rebalance
         "materiality": 5, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=3, lookback_days=14,
                pull_forward_prices=False)
    assert r.n_trims_triggered == 0
    # Lengthening lookback to 60 days → captured
    r2 = replay(journal_db=db, min_materiality=3, lookback_days=60,
                 pull_forward_prices=False)
    assert r2.n_trims_triggered == 1


# ============================================================
# parameter_sweep
# ============================================================
def test_parameter_sweep_returns_grid(tmp_path):
    from trader.reactor_backtest import parameter_sweep
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[], rebalance_decisions=[])
    results = parameter_sweep(
        journal_db=db,
        materialities=(3, 4),
        trim_pcts=(0.25, 0.50),
        pull_forward_prices=False,
    )
    # 2 × 2 = 4 configs
    assert len(results) == 4
    # Each config in result.config matches one cell of the grid
    grid = {(r.config["min_materiality"], r.config["trim_pct"])
            for r in results}
    assert grid == {(3, 0.25), (3, 0.50), (4, 0.25), (4, 0.50)}


# ============================================================
# Forward P&L impact sign
# ============================================================
def test_pnl_impact_positive_when_bearish_was_right(tmp_path, monkeypatch):
    """When forward return is negative (price fell after rebalance,
    BEARISH was correct), trimming SHOULD save P&L → impact positive."""
    from trader import reactor_backtest as rbt
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 4, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    # Mock the forward-price helper: -10% at 20d
    monkeypatch.setattr(
        rbt, "_pull_forward_returns",
        lambda symbol, date, horizons_days=(5, 10, 20):
            {5: -0.05, 10: -0.07, 20: -0.10},
    )
    r = rbt.replay(
        journal_db=db, min_materiality=4, trim_pct=0.5,
        pull_forward_prices=True,
    )
    e = r.trim_events[0]
    # Trimmed weight = 0.04. weight_delta = 0.04. forward_return = -0.10.
    # Saved = -weight_delta × forward_return = -0.04 × -0.10 = +0.004 = +0.4%
    assert e.pnl_impact_pct is not None
    assert e.pnl_impact_pct > 0
    assert abs(e.pnl_impact_pct - 0.004) < 1e-6


def test_pnl_impact_negative_when_bearish_was_wrong(tmp_path, monkeypatch):
    """When forward return is positive (price ROSE despite the
    BEARISH flag), trimming COSTS P&L → impact negative."""
    from trader import reactor_backtest as rbt
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 4, "direction": "BEARISH"},
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    monkeypatch.setattr(
        rbt, "_pull_forward_returns",
        lambda symbol, date, horizons_days=(5, 10, 20):
            {5: 0.03, 10: 0.06, 20: 0.10},
    )
    r = rbt.replay(
        journal_db=db, min_materiality=4, trim_pct=0.5,
        pull_forward_prices=True,
    )
    e = r.trim_events[0]
    # Saved = -0.04 × +0.10 = -0.004 → trimming COST 0.4%
    assert e.pnl_impact_pct is not None
    assert e.pnl_impact_pct < 0


# ============================================================
# Summary
# ============================================================
def test_summary_says_keep_collecting_on_zero_trims(tmp_path):
    from trader.reactor_backtest import replay
    db = tmp_path / "j.db"
    _seed_journal(db, signals=[
        {"symbol": "NVDA", "accession": "A1",
         "filed_at": "2026-04-30",
         "materiality": 2, "direction": "BEARISH"},  # below threshold
    ], rebalance_decisions=[
        {"ts": "2026-05-01T22:00:00", "ticker": "NVDA", "weight_pct": 8.0},
    ])
    r = replay(journal_db=db, min_materiality=4,
                pull_forward_prices=False)
    s = r.summary()
    # The summary must surface BOTH the absence of trims AND the
    # "keep running" recommendation
    assert "No trims" in s
    assert "Keep" in s or "keep" in s


# ============================================================
# CLI surface
# ============================================================
def test_dashboard_version_v3_72_0():
    p = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = p.read_text()
    # v3.72.0 changelog must remain in file history; sidebar caption
    # may have moved to a later patch.
    assert "v3.72.0" in text
    import re
    assert re.search(r'st\.caption\("v3\.[67]\d\.\d', text), \
        "sidebar must show some v3.6x.y or v3.7x.y version label"
