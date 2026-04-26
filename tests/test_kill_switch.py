"""Unit tests for kill switch."""
import os
import pytest
from pathlib import Path
import tempfile


@pytest.fixture
def temp_db_and_flag(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "test.db"
        flag_path = Path(d) / "halt_flag"
        monkeypatch.setattr("trader.journal.DB_PATH", db_path)
        monkeypatch.setattr("trader.kill_switch.KILL_FLAG_PATH", flag_path)
        yield flag_path


def test_no_halt_when_clean(temp_db_and_flag, monkeypatch):
    from trader import kill_switch
    monkeypatch.setattr(kill_switch, "ALPACA_KEY", "PK_TEST")
    monkeypatch.setattr(kill_switch, "USE_DEBATE", False)
    halt, reasons = kill_switch.check_kill_triggers(equity=100_000)
    assert not halt, reasons


def test_halt_when_no_alpaca_key(temp_db_and_flag, monkeypatch):
    from trader import kill_switch
    monkeypatch.setattr(kill_switch, "ALPACA_KEY", "")
    monkeypatch.setattr(kill_switch, "USE_DEBATE", False)
    halt, reasons = kill_switch.check_kill_triggers()
    assert halt
    assert any("ALPACA_API_KEY" in r for r in reasons)


def test_halt_when_debate_on_but_no_anthropic(temp_db_and_flag, monkeypatch):
    from trader import kill_switch
    monkeypatch.setattr(kill_switch, "ALPACA_KEY", "PK_TEST")
    monkeypatch.setattr(kill_switch, "ANTHROPIC_KEY", "")
    monkeypatch.setattr(kill_switch, "USE_DEBATE", True)
    halt, reasons = kill_switch.check_kill_triggers()
    assert halt
    assert any("ANTHROPIC_API_KEY" in r for r in reasons)


def test_arm_and_disarm_flag(temp_db_and_flag, monkeypatch):
    from trader import kill_switch
    monkeypatch.setattr(kill_switch, "ALPACA_KEY", "PK_TEST")
    monkeypatch.setattr(kill_switch, "USE_DEBATE", False)
    kill_switch.arm_kill_switch("unit test")
    halt, reasons = kill_switch.check_kill_triggers(equity=100_000)
    assert halt
    assert any("manual halt" in r for r in reasons)
    kill_switch.disarm_kill_switch()
    halt, reasons = kill_switch.check_kill_triggers(equity=100_000)
    assert not halt
