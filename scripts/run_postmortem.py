"""Nightly self-review entrypoint. Run after the daily orchestrator."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.postmortem import run_postmortem

if __name__ == "__main__":
    result = run_postmortem()
    print("\n=== POST-MORTEM ===\n")
    print(result.get("summary", ""))
