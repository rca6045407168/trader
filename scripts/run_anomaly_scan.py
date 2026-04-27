"""Run the anomaly scanner and print upcoming opportunities."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from datetime import date
from trader.anomalies import scan_anomalies


def main():
    today = date.today()
    print(f"=== ANOMALY SCAN  {today} ===\n")
    anomalies = scan_anomalies(today)
    if not anomalies:
        print("No active anomalies in the next 5 days.")
        return
    for a in anomalies:
        days_to = (a.fire_window[0] - today).days
        days_dur = (a.fire_window[1] - a.fire_window[0]).days
        print(f"• {a.name}  ({a.confidence} confidence, +{a.expected_alpha_bps}bps expected)")
        print(f"  Window: {a.fire_window[0]} → {a.fire_window[1]}  ({days_dur}d)")
        print(f"  Action: {a.expected_direction} {a.target_symbol}")
        print(f"  Why:    {a.rationale}\n")


if __name__ == "__main__":
    main()
