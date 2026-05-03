#!/bin/sh
# [v3.59.5 — TESTING_PRACTICES Cat 7] Mutation testing baseline.
#
# Mutmut introduces small bugs (flip > to <, change + to -, etc) into the
# source and checks whether the test suite catches them. Score = % of
# mutations the tests catch. Anything below 80% means tests aren't
# actually testing what you think they test.
#
# Run:
#   sh scripts/run_mutation_testing.sh
#
# Targets a SUBSET of high-leverage modules — running mutmut on the
# whole codebase takes ~6 hours. The subset below covers the modules
# most likely to be silently miscomputed (signal math, risk math,
# bootstrap CIs, performance metrics, sleeve gating).
#
# Install:  pip install mutmut
set -e

if ! command -v mutmut > /dev/null 2>&1; then
    echo "mutmut not installed. Install with: pip install mutmut"
    echo "Then re-run this script."
    exit 1
fi

cd "$(dirname "$0")/.."

# Targeted subset of modules where silent miscomputation is highest risk
TARGETS="src/trader/bootstrap_ci.py,src/trader/perf_metrics_v5.py,src/trader/spa_test.py,src/trader/v358_world_class.py,src/trader/drift_monitor.py,src/trader/signals.py,src/trader/risk_manager.py"

echo "=== Mutation testing baseline ==="
echo "Targets: $TARGETS"
echo "Test command: PYTHONPATH=src ANTHROPIC_API_KEY=test python -m pytest -x"
echo ""

# Run mutmut.
mutmut run \
    --paths-to-mutate="$TARGETS" \
    --runner="PYTHONPATH=src ANTHROPIC_API_KEY=test python -m pytest -x --tb=no -q" \
    --tests-dir=tests \
    || true   # mutmut exits non-zero when mutations survive — don't abort

echo ""
echo "=== Results ==="
mutmut results
echo ""
echo "Survival threshold: target <20% mutations surviving for solid coverage."
echo "Run 'mutmut show <id>' to inspect a specific surviving mutation."
echo "Run 'mutmut html' to generate a coverage report at html/index.html."
