#!/usr/bin/env bash
# resume.sh — re-enable trader workflows after a halt. Run AFTER you've fixed
# whatever caused the halt and verified CI is green on master.
#
# Usage:
#   bash scripts/resume.sh

set -euo pipefail

REPO=${GITHUB_REPOSITORY:-rca6045407168/trader}

echo "================================================================"
echo "  RESUME — re-enabling trader workflows"
echo "================================================================"
echo

if ! command -v gh &> /dev/null; then
  echo "❌ gh CLI not installed. Re-enable manually at:"
  echo "   https://github.com/${REPO}/actions"
  exit 1
fi

# Pre-flight: verify CI is green on master
echo "Checking master CI status..."
LATEST=$(gh run list --branch master --workflow=CI --limit 1 \
  --json status,conclusion --jq '.[0] | "\(.status):\(.conclusion)"' \
  --repo "${REPO}" 2>/dev/null || echo "unknown")

if [[ "$LATEST" != "completed:success" ]]; then
  echo
  echo "⚠ Master CI status is: $LATEST"
  echo "  Resume only after CI is green. If you're sure, force with:"
  echo "  FORCE=1 bash scripts/resume.sh"
  if [[ "${FORCE:-}" != "1" ]]; then
    exit 1
  fi
  echo "  (proceeding with FORCE=1)"
fi

echo "✓ CI clean."
echo

for workflow in trader-daily-run trader-hourly-reconcile trader-weekly-digest; do
  echo "  Enabling ${workflow}..."
  gh workflow enable "${workflow}" --repo "${REPO}" 2>&1 | head -2 || \
    echo "    (already enabled or not found)"
done

echo
echo "✓ Workflows re-enabled. Next scheduled run will fire at its cron time."
echo "  daily-run:        21:10 UTC Mon-Fri"
echo "  hourly-reconcile: every hour 14:00-20:00 UTC Mon-Fri"
echo "  weekly-digest:    Sunday 00:00 UTC"
echo
echo "Verify:"
echo "  gh workflow list --repo ${REPO} | grep -i trader"
