"""Single-use approval gate for local write actions."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import local_runner
from file_lock import exclusive


PATH = Path("./data/local-approvals.json")


def _read() -> dict[str, Any]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def propose(requester_id: int, action: str) -> dict[str, Any]:
    if action not in local_runner.APPROVED_ACTIONS:
        raise ValueError("local action belum di-allowlist")
    if not local_runner.enabled():
        raise RuntimeError("local actions sedang disabled")
    now = datetime.now(timezone.utc)
    record = {"id": "LAP-" + secrets.token_hex(4).upper(), "status": "PENDING", "requester_id": str(requester_id), "action": action, "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat()}
    with exclusive(PATH):
        data = _read(); data[record["id"]] = record
        PATH.parent.mkdir(parents=True, exist_ok=True); PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def approve(approval_id: str, user_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    with exclusive(PATH):
        return _approve_locked(approval_id, user_id)


def _approve_locked(approval_id: str, user_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _read(); record = data.get(approval_id.upper())
    if not record: raise ValueError("local approval tidak ditemukan")
    if record.get("status") != "PENDING": raise ValueError(f"approval sudah {record.get('status')}")
    if str(user_id) != record.get("requester_id"): raise PermissionError("hanya pembuat proposal yang boleh approve")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(record["expires_at"]):
        record["status"] = "EXPIRED"; PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); raise ValueError("approval sudah kedaluwarsa")
    record["status"] = "EXECUTING"; record["approved_at"] = datetime.now(timezone.utc).isoformat(); PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        result = local_runner.execute_action(record["action"])
        record["status"] = "EXECUTED"; record["executed_at"] = datetime.now(timezone.utc).isoformat()
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return record, result
    except Exception as exc:
        record["status"] = "UNKNOWN"; record["error"] = type(exc).__name__; PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); raise
