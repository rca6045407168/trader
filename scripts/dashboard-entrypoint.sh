#!/bin/sh
# v3.57.2 — dashboard container entrypoint.
# Step 1: pre-warm disk caches (HMM regime + portfolio + briefing) so the
#         FIRST user-load hits warm caches and renders in <50ms instead of
#         3.5s waiting for HMM EM training + yfinance + FRED + GARCH.
# Step 2: exec streamlit (PID 1) so SIGTERM from `docker stop` reaches it.
#
# Prewarm is best-effort: || true ensures a prewarm failure (no network,
# stale creds) does NOT block Streamlit from coming up. Lazy compute will
# still work on first user request.
set -e

echo "[entrypoint] running prewarm..."
python scripts/prewarm.py 2>&1 || true

echo "[entrypoint] starting streamlit..."
exec streamlit run scripts/dashboard.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
