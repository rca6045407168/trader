"""Persistent chat-thread storage for the Copilot (v3.56.0).

Each conversation is a ChatThread saved to data/copilot_chats/<uuid>.json.
The dashboard sidebar lists threads (newest-first); clicking one loads its
messages into st.session_state. New conversations auto-create a thread and
auto-title from the first user message.

Storage shape (one JSON per thread):
{
  "id": "<uuid4>",
  "title": "Why am I down today?",
  "created_at": "2026-05-03T...",
  "updated_at": "2026-05-03T...",
  "messages": [
    {"role": "user", "display_text": "...", "content": "..."},
    {"role": "assistant", "display_text": "...", "api_content": [...],
     "tool_calls": [{...}, ...]},
    ...
  ]
}

Thread JSON files are gitignored (data/ entirely is). Per-user, per-host.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

CHATS_DIR = DATA_DIR / "copilot_chats"


@dataclass
class ChatThread:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChatThread":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", "(untitled)"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            messages=d.get("messages", []),
        )


def _path_for(thread_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9-]", "", thread_id)[:40]
    return CHATS_DIR / f"{safe}.json"


def auto_title(first_message: str, max_chars: int = 50) -> str:
    """Generate a thread title from the first user message.
    Strips emojis/punctuation, truncates to ~50 chars at word boundary."""
    if not first_message:
        return "(empty)"
    txt = first_message.strip()
    # Strip leading emoji-ish chars
    txt = re.sub(r"^[^\w\s'\"!?\.,]+", "", txt).strip()
    if len(txt) <= max_chars:
        return txt or "(empty)"
    # Truncate at last word boundary before max_chars
    cut = txt[:max_chars]
    last_space = cut.rfind(" ")
    if last_space > max_chars * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(",.;:!?") + "…"


def new_thread() -> ChatThread:
    now = datetime.utcnow().isoformat()
    return ChatThread(
        id=str(uuid.uuid4()),
        title="(new chat)",
        created_at=now,
        updated_at=now,
        messages=[],
    )


def save_thread(thread: ChatThread) -> Path:
    """Persist thread to disk. Idempotent — overwrites existing file.
    Auto-derives title from first user message if title is still '(new chat)'."""
    if thread.title in ("(new chat)", "(empty)", "(untitled)") and thread.messages:
        first_user = next((m for m in thread.messages if m.get("role") == "user"), None)
        if first_user:
            thread.title = auto_title(
                first_user.get("display_text") or first_user.get("content") or ""
            )
    thread.updated_at = datetime.utcnow().isoformat()
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    p = _path_for(thread.id)
    p.write_text(json.dumps(thread.to_dict(), indent=2, default=str))
    return p


def load_thread(thread_id: str) -> Optional[ChatThread]:
    p = _path_for(thread_id)
    if not p.exists():
        return None
    try:
        return ChatThread.from_dict(json.loads(p.read_text()))
    except Exception:
        return None


def list_threads(limit: int = 100) -> list[ChatThread]:
    """Return threads sorted newest-first (by updated_at)."""
    if not CHATS_DIR.exists():
        return []
    threads: list[ChatThread] = []
    for p in CHATS_DIR.glob("*.json"):
        try:
            t = ChatThread.from_dict(json.loads(p.read_text()))
            threads.append(t)
        except Exception:
            continue
    threads.sort(key=lambda t: t.updated_at, reverse=True)
    return threads[:limit]


def delete_thread(thread_id: str) -> bool:
    p = _path_for(thread_id)
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except Exception:
        return False
