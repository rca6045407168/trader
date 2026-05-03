"""Cross-session Copilot memory + saved workflows (v3.57.1).

Two persistent files in `data/`:
  - `copilot_memory.md` — long-form user preferences / strategy context.
    Manually editable. Loaded into every Copilot system prompt.
  - `copilot_workflows.json` — named multi-step queries the user saved
    ("Morning briefing", "Pre-rebalance check"). One-click invocation.

Memory file format: free-form Markdown. Examples:
    # User preferences
    - Prefer monthly candles for charts
    - Risk tolerance: -25% drawdown is the pre-commit threshold
    # Strategy context
    - Currently running momentum_top15_mom_weighted_v1
    - Roth IRA target, no day-trading

Workflows JSON shape:
    [
      {"name": "Morning briefing", "prompts": ["Why am I up/down today?"]},
      {"name": "Pre-rebalance", "prompts": ["..", ".."]},
    ]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

MEMORY_FILE = DATA_DIR / "copilot_memory.md"
WORKFLOWS_FILE = DATA_DIR / "copilot_workflows.json"

DEFAULT_MEMORY = """# Copilot memory — long-form user preferences

This file is loaded into every Copilot system prompt. Edit freely.
Keep entries terse. Remove what's no longer relevant.

## User preferences

- Operator: Richard Chen, single-user, personal Roth IRA
- Risk tolerance: behavioral pre-commit at -25% drawdown
- Strategy: monthly rebalance, NOT high-frequency
- Default reporting: daily P&L vs SPY benchmark

## Strategy context

- Current LIVE: momentum_top15_mom_weighted_v1
- Honest PIT expectation: Sharpe 0.96, CAGR 19%, worst-DD -33%
- 3-gate promotion required before any new sleeve goes LIVE

## Don't suggest

- More frequent trading (overtrading is the #1 retail blow-up mode)
- Strategies in docs/CRITIQUE.md kill-list (already failed CPCV)
- Live LLM-driven trading (verified-failed pattern)
"""

DEFAULT_WORKFLOWS = [
    {
        "name": "🌅 Morning brief",
        "prompts": [
            "What changed overnight? Pull live portfolio, regime state, and upcoming events for the next 7 days. Summarize in 5 bullet points."
        ],
    },
    {
        "name": "📊 Why am I up/down?",
        "prompts": [
            "Why is my P&L moving today? Use get_portfolio_status + get_attribution_today. Give me top 3 contributors and top 3 detractors with sector context."
        ],
    },
    {
        "name": "⚠️ Pre-rebalance check",
        "prompts": [
            "Before tonight's rebalance: 1) what's the regime? 2) any freeze states active? 3) any earnings on held names this week? 4) what would the new top-15 picks likely be vs current holdings?"
        ],
    },
    {
        "name": "🔍 Strategy decay check",
        "prompts": [
            "Is my LIVE strategy showing signs of decay? Use get_sleeve_health and summarize. Compare current rolling Sharpe to PIT-honest baseline of 0.96. Flag if 90-day Sharpe < 0.5."
        ],
    },
]


def read_memory() -> str:
    """Read user-editable memory. Returns DEFAULT_MEMORY if file doesn't exist."""
    if not MEMORY_FILE.exists():
        return DEFAULT_MEMORY
    try:
        return MEMORY_FILE.read_text()
    except Exception:
        return DEFAULT_MEMORY


def write_memory(content: str) -> bool:
    """Persist edited memory. Returns True on success."""
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(content)
        return True
    except Exception:
        return False


def reset_memory_to_default() -> None:
    write_memory(DEFAULT_MEMORY)


def list_workflows() -> list[dict]:
    """Return saved workflows. Initializes file with defaults if missing."""
    if not WORKFLOWS_FILE.exists():
        save_workflows(DEFAULT_WORKFLOWS)
        return DEFAULT_WORKFLOWS
    try:
        data = json.loads(WORKFLOWS_FILE.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return DEFAULT_WORKFLOWS


def save_workflows(workflows: list[dict]) -> bool:
    try:
        WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
        WORKFLOWS_FILE.write_text(json.dumps(workflows, indent=2))
        return True
    except Exception:
        return False


def add_workflow(name: str, prompts: list[str]) -> bool:
    workflows = list_workflows()
    workflows.append({"name": name, "prompts": prompts})
    return save_workflows(workflows)


def delete_workflow(name: str) -> bool:
    workflows = list_workflows()
    new = [w for w in workflows if w.get("name") != name]
    if len(new) == len(workflows):
        return False
    return save_workflows(new)
