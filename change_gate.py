"""Approval records for merge/deploy handoff; execution remains outside Hermes."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from file_lock import exclusive


PATH = Path("./data/engineering/change-approvals.json")


def _read() -> dict[str, Any]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def propose(requester_id: int, target: str, action: str, reference: str, summary: str) -> dict[str, Any]:
    if target not in {"staging", "production"}:
        raise ValueError("target harus staging atau production")
    if action not in {"merge", "deploy"}:
        raise ValueError("action harus merge atau deploy")
    if not reference.strip() or not summary.strip():
        raise ValueError("reference dan ringkasan wajib diisi")
    approval_id = "CHG-" + secrets.token_hex(4).upper()
    now = datetime.now(timezone.utc)
    record = {"id": approval_id, "status": "PENDING", "requester_id": str(requester_id), "target": target, "action": action, "reference": reference.strip(), "summary": summary.strip(), "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=15)).isoformat()}
    with exclusive(PATH):
        data = _read(); data[approval_id] = record
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def approve(approval_id: str, user_id: int) -> dict[str, Any]:
    with exclusive(PATH):
        return _approve_locked(approval_id, user_id)


def _approve_locked(approval_id: str, user_id: int) -> dict[str, Any]:
    data = _read(); record = data.get(approval_id.upper())
    if not record: raise ValueError("change approval tidak ditemukan")
    if record.get("status") != "PENDING": raise ValueError(f"approval sudah {record.get('status')}")
    if str(user_id) != record.get("requester_id"): raise PermissionError("hanya pembuat proposal yang boleh approve")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(record["expires_at"]):
        record["status"] = "EXPIRED"; PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); raise ValueError("approval sudah kedaluwarsa")
    record["status"] = "APPROVED"; record["approved_at"] = datetime.now(timezone.utc).isoformat()
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return record
