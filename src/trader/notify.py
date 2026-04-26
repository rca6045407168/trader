"""Slack + console notifier."""
import requests
from .config import SLACK_WEBHOOK


def notify(msg: str, level: str = "info"):
    print(f"[{level.upper()}] {msg}")
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json={"text": f"*[trader/{level}]* {msg}"}, timeout=5)
        except Exception as e:
            print(f"[notify] slack failed: {e}")
