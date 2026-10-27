"""Safe Discord identity helpers for Hermes conversations."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import memory_store


def _configured_names() -> dict[str, str]:
    raw = os.getenv("HERMES_USER_NAMES", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(key): str(value)[:80] for key, value in parsed.items() if str(key).isdigit() and str(value).strip()}
    except json.JSONDecodeError:
        pass
    result: dict[str, str] = {}
    for part in re.split(r"[;,]", raw):
        if "=" in part:
            user_id, name = part.split("=", 1)
            if user_id.strip().isdigit() and name.strip():
                result[user_id.strip()] = name.strip()[:80]
    return result


def profile(user: Any) -> dict[str, str]:
    user_id = str(getattr(user, "id", ""))
    username = str(getattr(user, "name", "") or "").strip()
    display_name = str(getattr(user, "display_name", "") or getattr(user, "global_name", "") or username).strip()
    label = _configured_names().get(user_id) or display_name or username or "Pengguna Discord"
    return {"id": user_id, "name": label[:80], "username": username[:80], "display_name": display_name[:80]}


def remember_user(user: Any) -> dict[str, str]:
    current = profile(user)
    if current["id"].isdigit():
        memory_store.register_user(
            user_id=current["id"],
            display_name=current["name"],
            username=current["username"],
        )
    return current


def context_line(current: dict[str, str]) -> str:
    username = current.get("username", "")
    suffix = f" (@{username})" if username and username.lower() != current.get("name", "").lower() else ""
    return f"Pengguna Discord saat ini: {current.get('name', 'Pengguna Discord')}{suffix}. Jangan menebak identitas orang lain."


def asks_current_identity(text: str) -> bool:
    value = str(text or "").lower().strip()
    phrases = (
        "siapa saya", "siapa gue", "siapa gua", "siapa aku", "siapa yang ngobrol",
        "siapa yang chat", "siapa yang command", "kenal gue", "kenal kita",
        "tahu siapa", "tau siapa", "user siapa",
    )
    return any(phrase in value for phrase in phrases)


def answer(current: dict[str, str]) -> str:
    username = current.get("username", "")
    suffix = f" Username Discord: `@{username}`." if username else "."
    return f"👤 Yang sedang ngobrol dengan Hermes sekarang adalah **{current.get('name', 'Pengguna Discord')}**{suffix}"


def render_users(rows: list[dict[str, Any]]) -> str:
    lines = ["👥 **User yang sudah command ke Hermes**"]
    if not rows:
        return "Belum ada user yang tercatat."
    for row in rows:
        username = f" · @{row['username']}" if row.get("username") else ""
        lines.append(f"- **{row['display_name']}**{username} · {row['command_count']} command · terakhir `{row['last_seen_at']}`")
    return "\n".join(lines)[:1900]
