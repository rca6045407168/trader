#!/usr/bin/env bash
# halt.sh — operational pause button. Disables the daily-run + hourly-reconcile
# workflows so the strategy stops trading. Reversible via resume.sh.
#
# Usage:
#   bash scripts/halt.sh
#
# After running:
#   1. Investigate whatever made you halt
#   2. Fix in code, commit, push
#   3. Verify CI green
#   4. Resume with: bash scripts/resume.sh
#
# This does NOT close existing positions. To close everything, also run
# scripts/halt.py (Python — actually closes positions). halt.sh is the
# CRON-disable; halt.py is the position-close. Use both for a full halt.

set -euo pipefail

REPO=${GITHUB_REPOSITORY:-rca6045407168/trader}

echo "================================================================"
echo "  HALT — disabling all scheduled trader workflows"
echo "================================================================"
echo

if ! command -v gh &> /dev/null; then
  echo "❌ gh CLI not installed. Disable manually at:"
  echo "   https://github.com/${REPO}/actions"
  exit 1
fi

for workflow in trader-daily-run trader-hourly-reconcile trader-weekly-digest; do
  echo "  Disabling ${workflow}..."
  gh workflow disable "${workflow}" --repo "${REPO}" 2>&1 | head -2 || \
    echo "    (already disabled or not found)"
done

echo
echo "✓ Workflows disabled. Strategy will not place new orders."
echo
echo "What this does NOT do:"
echo "  - Does NOT close existing positions"
echo "  - Does NOT cancel open orders (run 'python scripts/halt.py' for that)"
echo
echo "To resume after fix:"
echo "  bash scripts/resume.sh"
echo
echo "Verify halt:"
echo "  gh workflow list --repo ${REPO} | grep -i trader"
