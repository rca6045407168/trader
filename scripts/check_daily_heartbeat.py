"""Cron heartbeat — alert when the daily orchestrator silently didn't fire.

Silent cron failure is the failure mode at the top of the operational-
risk list. Real evidence: at the time this script was built, May 4
(a Monday) had ZERO rows in the journal.runs table — meaning Monday's
daily orchestrator silently didn't fire and we wouldn't have known.

Schedule: launchd fires this script at 14:30 UTC (= 10:30 ET) on
weekdays, well after the 13:10 UTC daily-run window. By that time, a
healthy daily run should have written a `started` row to the journal's
`runs` table.

Idempotency: a date-stamped marker file `data/.last_heartbeat_alert`
suppresses repeat alerts within the same day. Re-running the script
multiple times → at most one alert per day.

Status detection:
- OK: a row with started_at on today's UTC date exists.
- ALERT: today is a trading day per market_session AND no row started
  today AND we haven't already alerted today.
- SKIP: weekend / holiday / pre-cutoff time.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

JOURNAL_DB = ROOT / "data" / "journal.db"
MARKER_FILE = ROOT / "data" / ".last_heartbeat_alert"


def _last_started_at(db_path: Path) -> str | None:
    """Most recent row in journal.runs.started_at. Returns ISO string
    or None if the table doesn't exist or is empty."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as c:
            row = c.execute(
                "SELECT started_at FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _already_alerted_today(today_iso: str) -> bool:
    if not MARKER_FILE.exists():
        return False
    try:
        return MARKER_FILE.read_text().strip() == today_iso
    except Exception:
        return False


def _mark_alerted_today(today_iso: str) -> None:
    try:
        MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        MARKER_FILE.write_text(today_iso)
    except Exception:
        pass


def check_heartbeat(now: datetime | None = None,
                     dry_run: bool = False) -> dict:
    """Returns a status dict. If status='alert', fires notify() (unless
    dry_run=True) and updates the marker."""
    from trader.market_session import market_session_now

    sess = market_session_now(now)
    today_iso = sess.et_now.date().isoformat()
    last_started = _last_started_at(JOURNAL_DB)

    out: dict = {
        "today": today_iso,
        "session": sess.label,
        "last_started_at": last_started,
        "is_trading_day": sess.last_trading_day == sess.et_now.date(),
    }

    # Skip on weekends + holidays
    if sess.label in ("CLOSED_WEEKEND", "CLOSED_HOLIDAY"):
        out["status"] = "skip"
        out["reason"] = f"non-trading day ({sess.label})"
        return out

    # Did a run start today?
    fired_today = (last_started is not None
                    and last_started.startswith(today_iso))
    out["fired_today"] = fired_today

    if fired_today:
        out["status"] = "ok"
        out["reason"] = (f"daily run started today at "
                         f"{last_started[:19]}")
        return out

    # Idempotency: don't re-alert within the same day
    if _already_alerted_today(today_iso):
        out["status"] = "already_alerted"
        out["reason"] = "marker file shows we already alerted today"
        return out

    # ALERT
    out["status"] = "alert"
    age_str = "unknown"
    if last_started:
        try:
            then = datetime.fromisoformat(last_started)
            age_h = (datetime.utcnow() - then).total_seconds() / 3600
            age_str = f"{age_h:.0f}h ago"
        except Exception:
            pass
    out["reason"] = (f"trading day {today_iso} but no daily run started "
                     f"today (last run: {last_started or 'NEVER'} "
                     f"= {age_str})")

    if dry_run:
        return out

    # Compose alert
    body = (
        f"⚠️ Daily orchestrator did NOT fire today.\n\n"
        f"Today: {today_iso} ({sess.label})\n"
        f"Last run started: {last_started or 'NEVER'} ({age_str})\n\n"
        f"Expected: a row in journal.runs with started_at LIKE "
        f"'{today_iso}%'.\n"
        f"Observed: most recent run started "
        f"{last_started or '(no rows)'}.\n\n"
        f"What this means:\n"
        f"- The launchd job com.trader.daily-run did not fire,\n"
        f"  OR fired but failed before writing the journal row,\n"
        f"  OR fired and wrote the row but on a different day boundary\n"
        f"  (timezone bug worth investigating).\n\n"
        f"Suggested actions:\n"
        f"1. Check launchctl list | grep trader-daily-run\n"
        f"2. Check ~/openclaw-workspace/trader-jobs/logs/ for today's run\n"
        f"3. If the run is missing entirely, kickstart it manually:\n"
        f"   launchctl kickstart -p gui/$(id -u)/com.trader.daily-run\n\n"
        f"This alert is sent at most once per day; the marker file\n"
        f"data/.last_heartbeat_alert prevents repeat sends."
    )

    try:
        from trader.notify import notify
        notify(body, level="warn",
                subject="[trader] heartbeat: daily run missing today")
    except Exception as e:
        out["notify_error"] = f"{type(e).__name__}: {e}"
    _mark_alerted_today(today_iso)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute status but don't fire alert or update marker")
    args = parser.parse_args()

    out = check_heartbeat(dry_run=args.dry_run)
    print(f"=== heartbeat: {out['status'].upper()} ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
    # Exit 0 even on alert — the alert IS the action; we don't want
    # launchd to retry-loop just because we fired one
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"heartbeat check failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(0)
