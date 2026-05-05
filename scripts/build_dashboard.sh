#!/usr/bin/env bash
# Rebuild + recreate the dashboard container with BUILD_INFO baked in.
# Use this instead of `docker compose build dashboard` directly so the
# image carries the commit hash + UTC timestamp the dashboard's
# build-info badge reads.
#
# Usage:
#     bash scripts/build_dashboard.sh             # build + restart
#     bash scripts/build_dashboard.sh --no-restart  # build only
#     bash scripts/build_dashboard.sh --status    # show current image build info
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--status" ]; then
  echo "=== Image inspect ==="
  docker images trader-dashboard --format \
    "table {{.Repository}}\t{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"
  echo
  echo "=== Container BUILD_INFO ==="
  docker exec trader-dashboard cat /app/BUILD_INFO.txt 2>&1 || \
    echo "(container not running or BUILD_INFO missing)"
  echo
  echo "=== Container status ==="
  docker ps --filter "name=trader-dashboard" --format \
    "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}"
  exit 0
fi

# Resolve current commit + UTC build time
BUILD_COMMIT=$(git rev-parse --short=12 HEAD 2>/dev/null || echo "unknown")
BUILD_TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "Building trader-dashboard image:"
echo "  commit:    $BUILD_COMMIT"
echo "  timestamp: $BUILD_TIMESTAMP"
echo

BUILD_COMMIT="$BUILD_COMMIT" \
BUILD_TIMESTAMP="$BUILD_TIMESTAMP" \
  docker compose build dashboard

if [ "${1:-}" = "--no-restart" ]; then
  echo
  echo "✓ image built; container NOT restarted (--no-restart)"
  echo "  to apply changes: docker compose up -d --force-recreate dashboard"
  exit 0
fi

echo
echo "Recreating container with new image..."
docker compose up -d --force-recreate dashboard

# Wait for health
echo "Waiting for dashboard to come up..."
for i in $(seq 1 30); do
  if curl -fs http://localhost:8501/_stcore/health > /dev/null 2>&1; then
    echo "✓ dashboard healthy at http://localhost:8501"
    docker exec trader-dashboard cat /app/BUILD_INFO.txt
    exit 0
  fi
  sleep 2
done

echo "⚠ dashboard didn't come up healthy in 60s; check 'docker compose logs dashboard'" >&2
exit 1
