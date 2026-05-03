"""[v3.59.2] Operational health checks per BLINDSPOTS.md §2.

Failures kill_switch.py + risk_manager.py don't catch:

  • Cron failed to fire (host down, scheduler bug, network)
  • Alpaca outage straddling rebalance window
  • LLM dependency outage
  • Library version drift / breaking change
  • Disk pressure on the journal

Each check is pure (no side effects), returns a dict with severity +
detail. The dashboard surfaces them; daily prewarm runs the suite and
emits one consolidated summary line.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import DATA_DIR


DB_PATH = DATA_DIR / "journal.db"


def daily_run_fired_today() -> dict:
    """Per BLINDSPOTS §2: is there a daily_snapshot row for today?
    If after 7pm UTC and no row → severity HIGH."""
    out = {"check": "daily_run_fired_today", "severity": "info"}
    if not DB_PATH.exists():
        out.update({"severity": "warn", "message": "journal.db not present"})
        return out
    today = datetime.utcnow().date().isoformat()
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as c:
            row = c.execute(
                "SELECT date, equity FROM daily_snapshot "
                "WHERE date = ? LIMIT 1", (today,)).fetchone()
    except Exception as e:
        out.update({"severity": "warn",
                    "message": f"query failed: {type(e).__name__}: {e}"})
        return out
    if row:
        out.update({"severity": "info",
                    "message": f"daily snapshot present (equity ${row[1]:,.0f})"})
    elif datetime.utcnow().hour >= 22:  # > 7pm PT (3am UTC next day rough)
        out.update({"severity": "high",
                    "message": "no daily_snapshot for today and it's past close"})
    else:
        out.update({"severity": "info",
                    "message": "no row yet but market hasn't closed"})
    return out


def journal_size_mb() -> dict:
    out = {"check": "journal_size_mb", "severity": "info"}
    if not DB_PATH.exists():
        out.update({"severity": "warn", "message": "journal.db not present"})
        return out
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    out["message"] = f"{size_mb:.1f} MB"
    out["size_mb"] = round(size_mb, 2)
    if size_mb > 500:
        out["severity"] = "warn"
        out["message"] += " (>500 MB; consider VACUUM)"
    return out


def backup_freshness() -> dict:
    """Per BLINDSPOTS §2: last successful nightly backup age."""
    out = {"check": "backup_freshness", "severity": "info"}
    bdir = DATA_DIR / "backups"
    if not bdir.exists():
        out.update({"severity": "high",
                    "message": "no backups/ directory — nightly backup never ran"})
        return out
    backups = sorted(bdir.glob("journal.*.db"))
    if not backups:
        out.update({"severity": "high", "message": "no backup files present"})
        return out
    latest = backups[-1]
    age_hours = (datetime.utcnow().timestamp() - latest.stat().st_mtime) / 3600
    out["message"] = f"latest: {latest.name} (age {age_hours:.0f}h)"
    out["latest"] = latest.name
    if age_hours > 36:
        out["severity"] = "warn"
        out["message"] += " (>36h — backup may have skipped)"
    return out


def alpaca_reachable() -> dict:
    """Quick HEAD-ish check. Best-effort; never blocks."""
    out = {"check": "alpaca_reachable", "severity": "info"}
    try:
        from .execute import get_client
        client = get_client()
        acc = client.get_account()
        equity = float(acc.equity)
        out["message"] = f"reachable; equity ${equity:,.0f}"
        out["equity"] = equity
    except Exception as e:
        out.update({"severity": "warn",
                    "message": f"could not reach Alpaca: {type(e).__name__}: {e}"})
    return out


def anthropic_reachable() -> dict:
    out = {"check": "anthropic_reachable", "severity": "info"}
    try:
        import anthropic  # noqa
        from .config import ANTHROPIC_KEY
        if not ANTHROPIC_KEY:
            out.update({"severity": "warn",
                        "message": "ANTHROPIC_API_KEY not set"})
            return out
        out["message"] = "client importable + key set (no live ping)"
    except Exception as e:
        out.update({"severity": "warn",
                    "message": f"anthropic SDK issue: {type(e).__name__}: {e}"})
    return out


def env_keys_documented() -> dict:
    """Per BLINDSPOTS §2 'API key rotation': flag if key has never been rotated."""
    out = {"check": "env_keys_age", "severity": "info"}
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env.exists():
        out.update({"severity": "info", "message": "no .env file"})
        return out
    age_days = (datetime.utcnow().timestamp() - env.stat().st_mtime) / 86400
    out["message"] = f".env mtime: {age_days:.0f} days old"
    out["age_days"] = round(age_days, 1)
    if age_days > 365:
        out["severity"] = "warn"
        out["message"] += " (>1 year — consider rotating Alpaca keys)"
    return out


def all_checks() -> list[dict]:
    """Run the full check suite. Returns list of result dicts."""
    return [
        daily_run_fired_today(),
        journal_size_mb(),
        backup_freshness(),
        alpaca_reachable(),
        anthropic_reachable(),
        env_keys_documented(),
    ]


def severity_summary(results: list[dict]) -> dict:
    """Reduce a list of checks to a single severity counter."""
    counts = {"high": 0, "warn": 0, "info": 0}
    for r in results:
        sev = r.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    if counts["high"] > 0:
        overall = "high"
    elif counts["warn"] > 0:
        overall = "warn"
    else:
        overall = "info"
    return {"overall": overall, **counts}
