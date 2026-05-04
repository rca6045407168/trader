#!/usr/bin/env bash
# Install + load the earnings-reactor launchd job (v3.68.1).
#
# Idempotent: if the job is already loaded, this unloads + reloads it
# (so plist edits in the repo take effect without manual surgery).
#
# Usage:
#     bash scripts/install_launchd_earnings.sh
#     bash scripts/install_launchd_earnings.sh --uninstall
#
# Logs land in ~/Library/Logs/trader-earnings-reactor.{out,err}.log
set -euo pipefail

LABEL="com.trader.earnings-reactor"
TRADER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_PLIST="$TRADER_DIR/infra/launchd/${LABEL}.plist"
DST_DIR="$HOME/Library/LaunchAgents"
DST_PLIST="$DST_DIR/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"

uninstall() {
  if launchctl list | grep -q "$LABEL"; then
    echo "  unloading $LABEL …"
    launchctl unload "$DST_PLIST" 2>/dev/null || true
  fi
  if [ -f "$DST_PLIST" ]; then
    echo "  removing $DST_PLIST"
    rm -f "$DST_PLIST"
  fi
  echo "✓ uninstalled"
}

if [ "${1:-}" = "--uninstall" ]; then
  uninstall
  exit 0
fi

# Sanity checks before installing
if [ ! -f "$SRC_PLIST" ]; then
  echo "ERROR: source plist missing: $SRC_PLIST" >&2
  exit 1
fi
if [ ! -x "$TRADER_DIR/.venv/bin/python" ]; then
  echo "ERROR: $TRADER_DIR/.venv/bin/python not found or not executable." >&2
  echo "       Create the venv first: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
if [ ! -f "$TRADER_DIR/.env" ]; then
  echo "WARN:  $TRADER_DIR/.env not found — reactor will run in stub-NEUTRAL mode" >&2
  echo "       (no Claude analysis without ANTHROPIC_API_KEY)" >&2
fi

mkdir -p "$DST_DIR" "$LOG_DIR"
chmod 700 "$LOG_DIR"

# Reload pattern: unload first if already loaded so plist edits stick
if launchctl list | grep -q "$LABEL"; then
  echo "  $LABEL already loaded — reloading"
  launchctl unload "$DST_PLIST" 2>/dev/null || true
fi

echo "  copying plist → $DST_PLIST"
cp "$SRC_PLIST" "$DST_PLIST"
chmod 644 "$DST_PLIST"

echo "  loading via launchctl"
launchctl load "$DST_PLIST"

if launchctl list | grep -q "$LABEL"; then
  echo
  echo "✓ installed: $LABEL (v3.68.3 — daemon mode)"
  echo
  echo "  Mode:    long-running daemon, polls every 5 min (REACTOR_WATCH_INTERVAL)"
  echo "  Restart: launchd KeepAlive=true respawns on crash (60s throttle)"
  echo "  Latency: <5 min from 8-K filing → email alert (M≥3 only)"
  echo
  echo "  Logs:    $LOG_DIR/trader-earnings-reactor.{out,err}.log"
  echo "  Status:  launchctl print gui/\$(id -u)/$LABEL | grep state"
  echo "  Disable: bash scripts/install_launchd_earnings.sh --uninstall"
  echo
  echo "  First poll fires in seconds (RunAtLoad=true). Tail the log:"
  echo "    tail -f $LOG_DIR/trader-earnings-reactor.out.log"
else
  echo "ERROR: launchctl load did not register the job" >&2
  exit 1
fi
