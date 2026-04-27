"""Tests for alerts module — verify body length passes stub guard + content is structured."""
from unittest.mock import patch, MagicMock


def _capture_notify_call():
    """Helper: patch notify and capture its arguments."""
    captured = {}

    def fake_notify(msg, level="info", subject=None, allow_stub=False):
        captured["msg"] = msg
        captured["level"] = level
        captured["subject"] = subject
        captured["allow_stub"] = allow_stub
        return {"console": True, "email": True, "to": "test@example.com"}

    return fake_notify, captured


def test_alert_halt_passes_stub_guard():
    """Halt alert body must be >80 chars to pass the v2.5 stub guard."""
    fake, captured = _capture_notify_call()
    with patch("trader.alerts.notify", side_effect=fake):
        from trader.alerts import alert_halt
        alert_halt("Reconciliation drift", detail={"unexpected": ["NVDA"], "missing": []})
    assert len(captured["msg"]) > 80
    assert "HALT" in captured["msg"]
    assert "Reconciliation drift" in captured["msg"]
    assert captured["level"] == "warn"
    assert "HALT" in captured["subject"]


def test_alert_kill_switch_includes_reasons():
    fake, captured = _capture_notify_call()
    with patch("trader.alerts.notify", side_effect=fake):
        from trader.alerts import alert_kill_switch
        alert_kill_switch(["equity drop 9.2%", "manual flag set"])
    assert "equity drop 9.2%" in captured["msg"]
    assert "manual flag set" in captured["msg"]
    assert "halt.py off" in captured["msg"]  # tells operator how to recover
    assert captured["level"] == "warn"


def test_alert_position_move():
    fake, captured = _capture_notify_call()
    with patch("trader.alerts.notify", side_effect=fake):
        from trader.alerts import alert_position_move
        alert_position_move("NVDA", -0.085, 6500.0, "down")
    assert "NVDA" in captured["msg"]
    assert "-8.50%" in captured["msg"]
    assert "$6,500.00" in captured["msg"]


def test_alert_drawdown_quotes_pre_committed_rules():
    """Drawdown alert should remind operator of pre-committed rules at DD thresholds."""
    fake, captured = _capture_notify_call()
    with patch("trader.alerts.notify", side_effect=fake):
        from trader.alerts import alert_drawdown
        alert_drawdown(-0.12, -0.10, 88000)
    assert "-8%" in captured["msg"]
    assert "-15%" in captured["msg"]
    assert "-20%" in captured["msg"]


def test_alert_api_failure():
    fake, captured = _capture_notify_call()
    with patch("trader.alerts.notify", side_effect=fake):
        from trader.alerts import alert_api_failure
        alert_api_failure("alpaca", "connection timeout")
    assert "alpaca" in captured["msg"]
    assert "connection timeout" in captured["msg"]
    assert captured["level"] == "error"
