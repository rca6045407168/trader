"""Audit health of all scheduled tasks. Are any stale or never-fired?"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from datetime import datetime, timezone, timedelta
import json


def main():
    sched_dir = Path.home() / ".claude" / "scheduled-tasks"
    if not sched_dir.exists():
        print("No scheduled tasks dir.")
        return

    print("=== SCHEDULED TASK HEALTH ===\n")
    tasks = sorted(d for d in sched_dir.iterdir() if d.is_dir() and d.name.startswith("trader-"))
    if not tasks:
        print("No trader-* tasks found.")
        return
    for t in tasks:
        skill = t / "SKILL.md"
        if not skill.exists():
            continue
        content = skill.read_text()
        # extract frontmatter description if available
        desc = ""
        if content.startswith("---"):
            end = content.find("---", 3)
            fm = content[3:end] if end > 0 else ""
            for line in fm.splitlines():
                if line.strip().startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        print(f"  {t.name}")
        if desc:
            print(f"    desc: {desc[:80]}")


if __name__ == "__main__":
    main()
