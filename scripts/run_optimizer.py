"""Walk-forward parameter sweep. Detects overfit before going live."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trader.meta_optimizer import walk_forward, recommend_params


def main():
    print("=" * 70)
    print("WALK-FORWARD PARAMETER SWEEP")
    print("  Train: 2015-01 to 2020-12  |  Test: 2021-01 to 2025-04")
    print("=" * 70)

    df = walk_forward(
        train_start="2015-01-01", train_end="2020-12-31",
        test_start="2021-01-01", test_end="2025-04-30",
        lookback_months_grid=(3, 6, 9, 12),
        top_n_grid=(5, 10, 15, 20),
    )
    print("\n--- ALL PARAMETER COMBOS (ranked by out-sample Sharpe) ---")
    print(df.to_string(index=False))
    print()

    rec = recommend_params(df)
    print("--- RECOMMENDATION ---")
    for k, v in rec.items():
        print(f"  {k:20s}  {v}")

    out = ROOT / "reports" / "walk_forward.csv"
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    main()
