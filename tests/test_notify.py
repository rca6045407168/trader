"""Tests for the email notifier (without actually sending)."""
from unittest.mock import patch, MagicMock


def test_notify_console_always_works(capsys):
    from trader.notify import notify
    result = notify("hello", level="info")
    captured = capsys.readouterr()
    assert "INFO" in captured.out
    assert "hello" in captured.out
    assert result["console"] is True


def test_notify_email_skipped_when_no_credentials(monkeypatch):
    monkeypatch.setattr("trader.notify.SMTP_USER", "")
    monkeypatch.setattr("trader.notify.SMTP_PASS", "")
    from trader.notify import notify
    result = notify("hello")
    assert result["email"] is False


def test_notify_email_attempted_when_credentials_set(monkeypatch):
    monkeypatch.setattr("trader.notify.SMTP_USER", "sender@gmail.com")
    monkeypatch.setattr("trader.notify.SMTP_PASS", "app-password")
    monkeypatch.setattr("trader.notify.EMAIL_TO", "richard.chen.1989@gmail.com")
    monkeypatch.setattr("trader.notify.EMAIL_FROM", "sender@gmail.com")

    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_smtp)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("trader.notify.smtplib.SMTP", return_value=fake_smtp):
        from trader.notify import notify
        result = notify("the test passed", level="info", subject="test subject")

    assert result["email"] is True
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("sender@gmail.com", "app-password")
    fake_smtp.send_message.assert_called_once()
    sent_msg = fake_smtp.send_message.call_args[0][0]
    assert sent_msg["To"] == "richard.chen.1989@gmail.com"
    assert "test subject" in sent_msg["Subject"]
    assert "the test passed" in sent_msg.get_content()
