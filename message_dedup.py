"""Persistent idempotency guards for Discord messages."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from file_lock import exclusive

STATE_FILE = Path(os.getenv("HERMES_MESSAGE_DEDUP_FILE", "./data/message-dedup.json"))
RETENTION_SECONDS = max(3600, int(os.getenv("HERMES_MESSAGE_DEDUP_RETENTION_HOURS", "24")) * 3600)
CONTENT_COOLDOWN_SECONDS = max(5, int(os.getenv("HERMES_MESSAGE_CONTENT_COOLDOWN_SECONDS", "45")))


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _content_key(channel_id: int | str, author_id: int | str, content: str) -> str:
    normalized = " ".join((content or "").casefold().split())
    digest = hashlib.sha256(f"{channel_id}|{author_id}|{normalized}".encode("utf-8")).hexdigest()
    return f"content:{digest}"


def claim(message_id: int | str, channel_id: int | str, author_id: int | str, content: str, now: float | None = None) -> bool:
    """Return True only for the first processable occurrence of a message."""
    current = time.time() if now is None else float(now)
    message_key = f"id:{message_id}"
    content_key = _content_key(channel_id, author_id, content)
    with exclusive(STATE_FILE):
        state = _load(STATE_FILE)
        entries = state["entries"]
        cutoff = current - RETENTION_SECONDS
        for key, value in list(entries.items()):
            if not isinstance(value, dict) or float(value.get("seen_at", 0)) < cutoff:
                entries.pop(key, None)
        duplicate = message_key in entries
        if not duplicate:
            previous = entries.get(content_key)
            duplicate = isinstance(previous, dict) and current - float(previous.get("seen_at", 0)) < CONTENT_COOLDOWN_SECONDS
        entries[message_key] = {"seen_at": current, "kind": "message"}
        if not duplicate:
            entries[content_key] = {"seen_at": current, "kind": "content"}
        _write(STATE_FILE, state)
        return not duplicate
