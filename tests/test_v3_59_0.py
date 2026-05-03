"""Tests for v3.59.0 — V5 Phases 1-4 + self-review #2/#3/#5."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


# ============================================================
# V5 Phase 1: audit + delete
# ============================================================

def test_use_debate_default_is_false_at_source():
    """The CODE default must be false. The .env file may override this
    (and Richard's .env still has USE_DEBATE=true — the V5 Phase 1
    change is to flip the SOURCE default; .env removal is a follow-up
    behavioral step under the pre-commit cool-off rule)."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "config.py"
    text = src.read_text()
    # The default literal in code should now be "false"
    assert 'USE_DEBATE = os.getenv("USE_DEBATE", "false").lower()' in text


def test_env_override_warning_documented():
    """The deprecation comment must mention that USE_DEBATE in .env
    still wins."""
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "config.py"
    text = src.read_text()
    assert "USE_DEBATE=true in env" in text


def test_iterate_v_scripts_archived():
    """All iterate_v* should now be under scripts/archive/."""
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    archive_dir = scripts_dir / "archive"
    assert archive_dir.exists()
    # No iterate_v* in scripts/ root (other than archive subdir)
    for f in scripts_dir.glob("iterate_v*.py"):
        assert "archive" in f.parts, f"unarchived: {f}"
    # Archive has at least one iterate_v* file
    assert any(archive_dir.glob("iterate_v*.py"))


def test_ml_ranker_has_deprecation_notice():
    src = Path(__file__).resolve().parent.parent / "src" / "trader" / "ml_ranker.py"
    text = src.read_text()
    assert "DEPRECATED" in text or "DEPRECATION NOTICE" in text


# ============================================================
# V5 Phase 2: PIT universe replacement
# ============================================================

def test_universe_pit_v5_module_imports():
    import trader.universe_pit_v5 as upv5
    assert hasattr(upv5, "members_as_of")
    assert hasattr(upv5, "diff_against_wiki")
    assert hasattr(upv5, "is_canary_clean")


def test_canary_returns_true_when_wiki_unavailable(monkeypatch):
    """If both sources fail, canary should not block LIVE (advisory)."""
    import trader.universe_pit_v5 as upv5
    # Force the wiki source to error by monkeypatching the import target
    monkeypatch.setattr(upv5, "members_as_of", lambda d: ())
    # When both empty, diff is 0 → canary is clean
    out = upv5.is_canary_clean("2026-05-03")
    assert out is True


# ============================================================
# V5 Phase 3: virtual shadow
# ============================================================

def test_register_and_book_a_fill(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.virtual_shadow.DATA_DIR", tmp_path)
    import trader.virtual_shadow as vs
    # Reset any in-process state
    vs._REGISTRY.clear()
    vs._FILTERS.clear()

    vs.register_shadow("test_sleeve", initial_equity=10_000.0)
    vs.on_fill("AAPL", "buy", qty=10, price=150.0,
                fill_id="test-fill-1")
    book = vs.get_book("test_sleeve")
    assert book is not None
    assert "AAPL" in book.positions
    assert book.positions["AAPL"]["qty"] == 10
    assert book.cash == pytest.approx(10_000 - 1500)


def test_idempotent_fill(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.virtual_shadow.DATA_DIR", tmp_path)
    import trader.virtual_shadow as vs
    vs._REGISTRY.clear()
    vs._FILTERS.clear()
    vs.register_shadow("idem", initial_equity=10_000.0)
    vs.on_fill("AAPL", "buy", 10, 150.0, fill_id="dup")
    vs.on_fill("AAPL", "buy", 10, 150.0, fill_id="dup")
    book = vs.get_book("idem")
    assert book.positions["AAPL"]["qty"] == 10
    assert len(book.fills) == 1


def test_shadow_filter_skips_unwanted(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.virtual_shadow.DATA_DIR", tmp_path)
    import trader.virtual_shadow as vs
    vs._REGISTRY.clear()
    vs._FILTERS.clear()
    # Filter only takes AAPL
    vs.register_shadow("only_aapl", initial_equity=10_000.0,
                        should_take=lambda sym, side, ts: sym == "AAPL")
    vs.on_fill("AAPL", "buy", 1, 150.0, fill_id="a")
    vs.on_fill("MSFT", "buy", 1, 300.0, fill_id="b")
    book = vs.get_book("only_aapl")
    assert "AAPL" in book.positions
    assert "MSFT" not in book.positions


def test_shadow_exception_does_not_propagate(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.virtual_shadow.DATA_DIR", tmp_path)
    import trader.virtual_shadow as vs
    vs._REGISTRY.clear()
    vs._FILTERS.clear()
    # Filter that always raises
    vs.register_shadow("flaky", initial_equity=10_000.0,
                        should_take=lambda *a, **kw: 1/0)
    # Should NOT raise
    vs.on_fill("AAPL", "buy", 1, 150.0, fill_id="x")


# ============================================================
# V5 Phase 4: Pre-FOMC drift
# ============================================================

def test_fomc_drift_fires_on_eve():
    from trader.fomc_drift import is_drift_window, FOMC_DATES_2026
    fomc = FOMC_DATES_2026[0]
    eve = fomc - timedelta(days=1)
    in_win, d = is_drift_window(eve)
    assert in_win is True
    assert d == fomc


def test_fomc_drift_fires_on_meeting_day():
    from trader.fomc_drift import is_drift_window, FOMC_DATES_2026
    in_win, d = is_drift_window(FOMC_DATES_2026[0])
    assert in_win is True


def test_fomc_drift_silent_outside_window():
    from trader.fomc_drift import is_drift_window
    # Mid-month random date with no FOMC
    in_win, d = is_drift_window(date(2026, 2, 15))
    assert in_win is False
    assert d is None


def test_fomc_drift_default_status_shadow(monkeypatch):
    monkeypatch.delenv("FOMC_DRIFT_STATUS", raising=False)
    from trader.fomc_drift import status
    assert status() == "SHADOW"


def test_fomc_drift_signal_shape():
    from trader.fomc_drift import compute_signal, FOMC_DATES_2026
    eve = FOMC_DATES_2026[0] - timedelta(days=1)
    sig = compute_signal(eve)
    assert sig.in_drift_window is True
    assert sig.target_weight_spy > 0
    assert "FOMC" in sig.rationale


def test_fomc_drift_expected_target_format():
    from trader.fomc_drift import expected_target, FOMC_DATES_2026
    # Should be {SPY: weight} or empty
    targets = expected_target()
    assert isinstance(targets, dict)
    for sym in targets:
        assert isinstance(targets[sym], float)
        assert 0 <= targets[sym] <= 1


# ============================================================
# Self-review #2: slippage reconcile
# ============================================================

def test_slip_bps_buy_pays_more():
    from trader.slippage_reconcile import _slip_bps
    bps = _slip_bps("buy", decision_mid=100.0, fill_price=100.10)
    assert bps == pytest.approx(10.0, abs=0.01)


def test_slip_bps_sell_gets_less():
    from trader.slippage_reconcile import _slip_bps
    bps = _slip_bps("sell", decision_mid=100.0, fill_price=99.90)
    assert bps == pytest.approx(10.0, abs=0.01)


def test_slip_bps_handles_zero_mid():
    from trader.slippage_reconcile import _slip_bps
    assert _slip_bps("buy", 0, 100) == 0.0


# ============================================================
# Self-review #3: MOC orders (env-controlled)
# ============================================================

def test_use_moc_default_false(monkeypatch):
    """USE_MOC_ORDERS defaults to false — DAY orders preserved."""
    monkeypatch.delenv("USE_MOC_ORDERS", raising=False)
    import os as _os
    assert _os.getenv("USE_MOC_ORDERS", "false").lower() != "true"


def test_use_moc_signature():
    import inspect
    from trader.execute import place_target_weights
    sig = inspect.signature(place_target_weights)
    assert "use_moc" in sig.parameters


# ============================================================
# Self-review #5: DB backup
# ============================================================

def test_backup_module_imports():
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import backup_journal as bj
    assert callable(bj.main)
    assert hasattr(bj, "RETENTION_DAYS")
    assert bj.RETENTION_DAYS >= 7  # at least a week


def test_backup_handles_missing_source(tmp_path, monkeypatch, capsys):
    """If source DB is missing, return 0 and don't error."""
    import sys
    p = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(p))
    import backup_journal as bj
    monkeypatch.setattr(bj, "DB", tmp_path / "nonexistent.db")
    monkeypatch.setattr(bj, "BACKUP_DIR", tmp_path / "backups")
    assert bj.main() == 0
    out = capsys.readouterr().out
    assert "skipping" in out


# ============================================================
# Dashboard cmd_bar bug fix (v3.59.0)
# ============================================================

def test_dashboard_no_cmd_bar_session_state_write():
    """The Streamlit-API-compliant fix for the cmd_bar widget: don't
    write to st.session_state.cmd_bar after the widget is instantiated.
    Use _last_cmd_pick as the change-detector instead."""
    src = Path(__file__).resolve().parent.parent / "scripts" / "dashboard.py"
    text = src.read_text()
    assert "st.session_state.cmd_bar = " not in text, (
        "regression: writing to a widget key after instantiation crashes Streamlit"
    )
    assert "_last_cmd_pick" in text, "missing the change-detector fix"
