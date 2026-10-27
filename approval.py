"""Single-use approval queue and narrowly scoped ERP write executor."""

from __future__ import annotations

import json
import os
import re
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import monitor
from file_lock import exclusive

APPROVAL_FILE = Path(os.getenv("HERMES_APPROVAL_FILE", "./data/pending-approvals.json"))
APPROVAL_TTL_MINUTES = max(1, int(os.getenv("HERMES_APPROVAL_TTL_MINUTES", "10")))
ALLOWED_PATHS = {
    "/api/erp/ingredients": {"POST"},
    "/api/erp/products": {"POST"},
    "/api/erp/purchases": {"POST"},
    "/api/erp/sales": {"POST"},
    "/api/erp/stock-counts": {"POST"},
    "/api/erp/preparations": {"POST"},
}
ALLOWED_ITEM = re.compile(r"^/api/erp/(ingredients|products)/[1-9][0-9]*$")
ALLOWED_NESTED = re.compile(r"^/api/erp/(products/[1-9][0-9]*/recipe|products/[1-9][0-9]*/preparations|preparations/[1-9][0-9]*/recipe)$")
ALLOWED_BATCH = re.compile(r"^/api/erp/preparations/[1-9][0-9]*/batches$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, dict[str, Any]]) -> None:
    APPROVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = APPROVAL_FILE.with_suffix(APPROVAL_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(APPROVAL_FILE)


def validate_action(method: str, path: str, body: Any) -> tuple[str, str, Any]:
    method = method.upper().strip()
    if method not in {"POST", "PATCH", "PUT"}:
        raise ValueError("method hanya boleh POST, PATCH, atau PUT")
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
        raise ValueError("path endpoint tidak valid")
    allowed = ALLOWED_PATHS.get(path)
    if allowed is None and ALLOWED_ITEM.fullmatch(path):
        allowed = {"PATCH"}
    if allowed is None and ALLOWED_NESTED.fullmatch(path):
        allowed = {"PUT"}
    if allowed is None and ALLOWED_BATCH.fullmatch(path):
        allowed = {"POST"}
    if not allowed or method not in allowed:
        raise ValueError("endpoint atau method belum masuk allowlist approval")
    if not isinstance(body, dict) or not body:
        raise ValueError("body harus JSON object dan tidak boleh kosong")
    return method, path, body


def create(*, requester_id: int, requester_name: str, method: str, path: str, body: Any,
           automatic_reason: str | None = None) -> dict[str, Any]:
    method, path, body = validate_action(method, path, body)
    approval_id = "HMR-" + secrets.token_hex(4).upper()
    created = _now()
    record = {
        "id": approval_id,
        "status": "PENDING",
        "requester_id": str(requester_id),
        "requester_name": requester_name,
        "method": method,
        "path": path,
        "body": body,
        "approval_mode": "AUTOMATIC" if automatic_reason else "MANUAL",
        "automatic_reason": automatic_reason,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=APPROVAL_TTL_MINUTES)).isoformat(),
    }
    with exclusive(APPROVAL_FILE):
        data = _read()
        data[approval_id] = record
        _write(data)
    return record


def _get_active(approval_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = _read()
    record = data.get(approval_id.upper())
    if not record:
        raise ValueError("approval ID tidak ditemukan")
    if record.get("status") != "PENDING":
        raise ValueError(f"approval sudah berstatus {record.get('status')}")
    if _now() >= datetime.fromisoformat(record["expires_at"]):
        record["status"] = "EXPIRED"
        _write(data)
        raise ValueError("approval sudah kedaluwarsa")
    return record, data


def approve_and_execute(approval_id: str, approver_id: int) -> tuple[dict[str, Any], Any]:
    # Hold a cross-process lock through the remote request so two retries cannot
    # both observe PENDING and submit the same ERP operation.
    with exclusive(APPROVAL_FILE):
        return _approve_and_execute_locked(approval_id, approver_id)


class AutomaticExecutionError(RuntimeError):
    def __init__(self, approval_id: str, cause_name: str):
        super().__init__(f"automatic ERP execution result unknown for {approval_id} ({cause_name})")
        self.approval_id = approval_id


def execute_automatically(*, requester_id: int, requester_name: str, method: str,
                          path: str, body: Any, policy_reason: str) -> tuple[dict[str, Any], Any]:
    """Execute only a write already admitted by the separate low-risk policy."""
    record = create(
        requester_id=requester_id, requester_name=requester_name, method=method,
        path=path, body=body, automatic_reason=policy_reason,
    )
    try:
        return approve_and_execute(record["id"], requester_id)
    except Exception as exc:
        stored = _read().get(record["id"], {})
        if stored.get("status") in {"EXECUTING", "UNKNOWN"}:
            raise AutomaticExecutionError(record["id"], type(exc).__name__) from exc
        raise


def _approve_and_execute_locked(approval_id: str, approver_id: int) -> tuple[dict[str, Any], Any]:
    record, data = _get_active(approval_id)
    if str(approver_id) != record["requester_id"]:
        raise PermissionError("approval hanya boleh dikonfirmasi oleh pembuat proposal")
    username = os.getenv("KASAPINK_OPERATOR_USERNAME", "").strip()
    password = os.getenv("KASAPINK_OPERATOR_PASSWORD", "")
    base_url = os.getenv("KASAPINK_BASE_URL", "").strip().rstrip("/")
    if not username or not password or not base_url:
        raise RuntimeError("akun operator atau koneksi ERP belum dikonfigurasi")

    # Consume before the network request: retrying cannot duplicate this approval.
    record["status"] = "EXECUTING"
    record["approved_at"] = _now().isoformat()
    _write(data)
    try:
        opener, headers = monitor.login_kasapink(base_url, username, password)
        payload = json.dumps(record["body"], ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}{record['path']}", data=payload,
            headers={**headers, "Content-Type": "application/json"}, method=record["method"]
        )
        with opener.open(request, timeout=20) as response:
            raw = response.read()
            result = json.loads(raw.decode("utf-8")) if raw else {}
        record["status"] = "EXECUTED"
        record["executed_at"] = _now().isoformat()
        _write(data)
        return record, result
    except Exception as exc:
        # A timeout/disconnect may happen after the ERP committed the request.
        # Never invite automatic retry: require read-only reconciliation first.
        record["status"] = "UNKNOWN"
        record["unknown_at"] = _now().isoformat()
        record["error"] = type(exc).__name__
        _write(data)
        raise


def deny(approval_id: str, user_id: int) -> dict[str, Any]:
    with exclusive(APPROVAL_FILE):
        record, data = _get_active(approval_id)
        if str(user_id) != record["requester_id"]:
            raise PermissionError("approval hanya boleh dibatalkan oleh pembuat proposal")
        record["status"] = "DENIED"
        record["denied_at"] = _now().isoformat()
        _write(data)
        return record


def get_status(approval_id: str, requester_id: int) -> dict[str, Any]:
    """Read an approval's lifecycle without returning its submitted body."""
    record = _read().get(approval_id.upper())
    if not record:
        raise ValueError("approval ID tidak ditemukan")
    if str(requester_id) != record.get("requester_id"):
        raise PermissionError("status approval hanya bisa dilihat pembuatnya")
    return {key: record.get(key) for key in (
        "id", "status", "approval_mode", "method", "path", "created_at", "expires_at",
        "approved_at", "executed_at", "unknown_at", "denied_at", "error",
    ) if key in record}


def describe(record: dict[str, Any]) -> str:
    body = json.dumps(record["body"], ensure_ascii=False, separators=(", ", ": "))
    return (
        f"**Approval `{record['id']}` — menunggu konfirmasi**\n"
        f"Target: `ERP Henka`\nMetode: `{record['method']}`\n"
        f"Endpoint: `{record['path']}`\nData: `{body}`\n"
        f"Berlaku sampai: `{record['expires_at']}`\n\n"
        f"Jika sudah benar, kirim `/approve {record['id']}`. "
        f"Untuk membatalkan: `/deny {record['id']}`."
    )
