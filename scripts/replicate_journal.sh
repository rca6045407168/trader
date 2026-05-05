#!/usr/bin/env bash
# v3.73.9 — Journal replication.
#
# Closes the v3.73.4 DD's Tier-1 ops item: "data/journal.db is on the
# same laptop as the orchestrator. If the laptop dies, the journal
# dies with it."
#
# Strategy: daily SQLite backup + rotation, written to iCloud Drive
# (auto-synced off-machine). Uses sqlite3's `.backup` command (NOT
# cp) so it's safe against an in-flight write — the backup is
# transactionally consistent even while the dashboard is open.
#
# Retention: 7 daily snapshots + 4 weekly (~30 days history). Old
# files are pruned in the same script so disk doesn't grow unbounded.
#
# Idempotent — safe to run multiple times per day; later runs
# overwrite that day's snapshot.
#
# Usage:
#   ./replicate_journal.sh          # one-shot
#   ./replicate_journal.sh --check  # report status, don't write

set -euo pipefail

REPO="$HOME/trader"
SRC="$REPO/data/journal.db"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
DEST_DIR="$ICLOUD/trader-journal-backup"
LOG_FILE="$HOME/Library/Logs/trader-journal-replicate.log"
TODAY=$(date +%Y-%m-%d)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=true
fi

# Pre-flight
if [[ ! -f "$SRC" ]]; then
  echo "[$TS] ERROR: journal not found at $SRC" | tee -a "$LOG_FILE" >&2
  exit 2
fi
if [[ ! -d "$ICLOUD" ]]; then
  echo "[$TS] ERROR: iCloud Drive not mounted at $ICLOUD" | tee -a "$LOG_FILE" >&2
  exit 2
fi

mkdir -p "$DEST_DIR"

if $CHECK_ONLY; then
  echo "src:    $SRC ($(stat -f%z "$SRC") bytes, $(stat -f%Sm "$SRC"))"
  echo "dest:   $DEST_DIR"
  echo "rows:   $(ls -1 "$DEST_DIR"/*.db 2>/dev/null | wc -l) backup files"
  ls -lh "$DEST_DIR"/ 2>/dev/null | tail -8
  exit 0
fi

DEST="$DEST_DIR/journal-$TODAY.db"

# Use SQLite's online backup, NOT file copy. .backup acquires a
# read lock per page and produces a consistent copy even if the
# orchestrator is mid-write.
sqlite3 "$SRC" ".backup '$DEST'"

# Verify the backup opens (sanity)
ROWS=$(sqlite3 "$DEST" "SELECT COUNT(*) FROM runs;")
echo "[$TS] backup ok: $DEST (rows in runs: $ROWS)" >> "$LOG_FILE"

# Retention: keep last 7 daily snapshots
cd "$DEST_DIR"
DAILY_COUNT=$(ls -1 journal-????-??-??.db 2>/dev/null | wc -l | tr -d ' ')
if (( DAILY_COUNT > 7 )); then
  ls -1t journal-????-??-??.db | tail -n +8 | xargs rm -f
  PRUNED=$((DAILY_COUNT - 7))
  echo "[$TS] pruned $PRUNED old daily backups" >> "$LOG_FILE"
fi

# Weekly snapshot on Mondays (preserved beyond the 7-day rolling)
DOW=$(date +%u)
if [[ "$DOW" == "1" ]]; then
  WEEKLY="$DEST_DIR/weekly-$(date +%Y-W%V).db"
  cp "$DEST" "$WEEKLY"
  # Keep last 4 weekly snapshots
  cd "$DEST_DIR"
  WEEKLY_COUNT=$(ls -1 weekly-*.db 2>/dev/null | wc -l | tr -d ' ')
  if (( WEEKLY_COUNT > 4 )); then
    ls -1t weekly-*.db | tail -n +5 | xargs rm -f
  fi
  echo "[$TS] weekly snapshot: $WEEKLY" >> "$LOG_FILE"
fi

echo "[$TS] complete" >> "$LOG_FILE"
echo "ok: $DEST"
