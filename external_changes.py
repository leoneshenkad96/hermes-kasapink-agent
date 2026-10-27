"""Approval-gated, allowlisted write actions for Hermes external tools.

There is no arbitrary shell or arbitrary API payload path here. Every action
has a fixed target, validated parameters, an expiring approval, audit fields,
and a single-use execution record.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import external_adapters
from file_lock import exclusive


STATE_FILE = Path(os.getenv("HERMES_EXTERNAL_CHANGE_FILE", "./data/external-changes.json"))
REPO_ROOT = Path(os.getenv("KASAPINK_REPO_ROOT", ".")).resolve()
TTL_MINUTES = max(5, int(os.getenv("HERMES_EXTERNAL_CHANGE_TTL_MINUTES", "15")))
PROTECTED_BRANCHES = {"main", "master", "production", "develop"}
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,90}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _redact(value: str) -> str:
    return re.sub(r"(?i)(token|secret|password|api[-_]?key)\s*[:=]\s*[^\s,}]+", r"\1=[REDACTED]", value)[:700]


def _validate(service: str, action: str, payload: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    service = service.strip().lower()
    action = action.strip().lower()
    if not isinstance(payload, dict):
        raise ValueError("payload harus JSON object")

    if service == "github" and action == "issue_create":
        title, body = str(payload.get("title", "")).strip(), str(payload.get("body", "")).strip()
        if not title or not body or len(title) > 200 or len(body) > 20_000:
            raise ValueError("issue memerlukan title/body yang valid")
        return service, action, {"title": title, "body": body}, "Membuat issue GitHub; ini komunikasi eksternal dan dapat memicu pekerjaan tim."

    if service == "github" and action == "pr_comment":
        number, body = payload.get("number"), str(payload.get("body", "")).strip()
        if not isinstance(number, int) or number <= 0 or not body or len(body) > 20_000:
            raise ValueError("pr_comment memerlukan number positif dan body")
        return service, action, {"number": number, "body": body}, "Menulis komentar pada pull request GitHub."

    if service == "github" and action == "pr_create":
        head, title, body = str(payload.get("head", "")).strip(), str(payload.get("title", "")).strip(), str(payload.get("body", "")).strip()
        base = str(payload.get("base", "main")).strip()
        if not BRANCH_RE.fullmatch(head) or not BRANCH_RE.fullmatch(base) or not title or not body:
            raise ValueError("pr_create memerlukan head/base branch, title, dan body")
        return service, action, {"head": head, "base": base, "title": title[:200], "body": body[:20_000]}, "Membuat pull request GitHub."

    if service == "vercel" and action in {"preview_deploy", "production_deploy"}:
        if not REPO_ROOT.is_dir() or not (REPO_ROOT / "package.json").is_file():
            raise ValueError("root repo ERP belum tersedia atau package.json tidak ditemukan")
        risk = "Membuat deployment preview Vercel." if action == "preview_deploy" else "Membuat deployment production Vercel dan berpotensi mengubah traffic/domain production."
        return service, action, {"project": external_adapters.VERCEL_PROJECT, "repo_root": str(REPO_ROOT)}, risk

    if service == "neon" and action == "branch_create":
        name = str(payload.get("name", "")).strip()
        parent = str(payload.get("parent", "")).strip()
        if not BRANCH_RE.fullmatch(name) or name.lower() in PROTECTED_BRANCHES:
            raise ValueError("nama branch Neon tidak valid atau protected")
        if parent and not BRANCH_RE.fullmatch(parent):
            raise ValueError("parent branch Neon tidak valid")
        return service, action, {"name": name, "parent": parent, "project_id": external_adapters.NEON_PROJECT_ID}, "Membuat branch Neon baru dengan compute read-write; tidak mengubah production branch."

    if service == "git" and action == "branch_create":
        name = str(payload.get("name", "")).strip()
        if not BRANCH_RE.fullmatch(name) or name.lower() in PROTECTED_BRANCHES:
            raise ValueError("nama branch Git tidak valid atau protected")
        return service, action, {"name": name}, "Membuat branch lokal baru; belum push ke remote."

    if service == "git" and action == "commit":
        message = str(payload.get("message", "")).strip()
        if not message or len(message) > 200:
            raise ValueError("commit memerlukan message 1-200 karakter")
        return service, action, {"message": message}, "Membuat commit dari perubahan yang sudah staged; tidak melakukan push."

    if service == "git" and action == "push_branch":
        name = str(payload.get("name", "")).strip()
        if not BRANCH_RE.fullmatch(name) or name.lower() in PROTECTED_BRANCHES:
            raise ValueError("push hanya boleh ke branch non-protected yang valid")
        return service, action, {"name": name}, "Push branch non-protected ke origin GitHub."

    raise ValueError("service/action belum ada di allowlist write Hermes")


def create(service: str, action: str, payload: dict[str, Any], requester_id: int | str) -> dict[str, Any]:
    service, action, payload, risk = _validate(service, action, payload)
    change_id = "EXT-" + secrets.token_hex(4).upper()
    now = _now()
    record = {
        "id": change_id,
        "status": "AWAITING_APPROVAL",
        "service": service,
        "action": action,
        "payload": payload,
        "risk": risk,
        "requester_id": str(requester_id),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=TTL_MINUTES)).isoformat(),
    }
    with exclusive(STATE_FILE):
        data = _read(); data[change_id] = record; _write(data)
    return record


def get(change_id: str, requester_id: int | str | None = None) -> dict[str, Any]:
    record = _read().get(change_id.upper())
    if not record:
        raise ValueError("external change tidak ditemukan")
    if requester_id is not None and str(record.get("requester_id")) != str(requester_id):
        raise PermissionError("external change hanya bisa dilihat pembuatnya")
    return record


def _run(command: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    try:
        result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("external command timeout") from exc
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(_redact(output or f"command exit {result.returncode}"))
    return _redact(output)


def _execute(record: dict[str, Any]) -> dict[str, Any]:
    service, action, payload = record["service"], record["action"], record["payload"]
    if service == "github" and action == "issue_create":
        output = external_adapters._run("gh", ["issue", "create", "--repo", external_adapters.GITHUB_REPOSITORY, "--title", payload["title"], "--body", payload["body"]])
        return {"summary": "GitHub issue dibuat.", "evidence": output}
    if service == "github" and action == "pr_comment":
        output = external_adapters._run("gh", ["pr", "comment", str(payload["number"]), "--repo", external_adapters.GITHUB_REPOSITORY, "--body", payload["body"]])
        return {"summary": "Komentar pull request GitHub dibuat.", "evidence": output}
    if service == "github" and action == "pr_create":
        output = external_adapters._run("gh", ["pr", "create", "--repo", external_adapters.GITHUB_REPOSITORY, "--head", payload["head"], "--base", payload["base"], "--title", payload["title"], "--body", payload["body"]])
        return {"summary": "Pull request GitHub dibuat.", "evidence": output}
    if service == "vercel" and action in {"preview_deploy", "production_deploy"}:
        args = ["deploy", "--cwd", payload["repo_root"], "--project", payload["project"], "--yes", "--non-interactive", "--json"]
        if action == "production_deploy":
            args.append("--prod")
        output = external_adapters._run("vercel", args, timeout=900)
        return {"summary": "Deployment Vercel selesai dipanggil.", "evidence": output}
    if service == "neon" and action == "branch_create":
        args = ["branches", "create", "--profile", external_adapters.NEON_PROFILE, "--project-id", payload["project_id"], "--name", payload["name"], "--type", "read_write", "--no-secrets", "-o", "json"]
        if payload.get("parent"):
            args.extend(["--parent", payload["parent"]])
        output = external_adapters._run("neon", args)
        return {"summary": "Branch Neon dibuat.", "evidence": output}
    if service == "git" and action == "branch_create":
        return {"summary": "Branch Git lokal dibuat.", "evidence": _run(["git", "switch", "-c", payload["name"]], cwd=REPO_ROOT)}
    if service == "git" and action == "commit":
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(REPO_ROOT), capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if staged.returncode == 0:
            raise RuntimeError("tidak ada perubahan staged")
        return {"summary": "Commit Git dibuat.", "evidence": _run(["git", "commit", "-m", payload["message"]], cwd=REPO_ROOT)}
    if service == "git" and action == "push_branch":
        return {"summary": "Branch Git dipush ke origin.", "evidence": _run(["git", "push", "origin", payload["name"]], cwd=REPO_ROOT)}
    raise RuntimeError("external action belum memiliki executor")


def approve_and_execute(change_id: str, requester_id: int | str) -> dict[str, Any]:
    with exclusive(STATE_FILE):
        data = _read()
        record = data.get(change_id.upper())
        if not record:
            raise ValueError("external change tidak ditemukan")
        if str(record.get("requester_id")) != str(requester_id):
            raise PermissionError("external change hanya bisa dijalankan pembuatnya")
        if record.get("status") != "AWAITING_APPROVAL":
            raise ValueError(f"change berstatus {record.get('status')}")
        if _now() >= datetime.fromisoformat(record["expires_at"]):
            record["status"] = "EXPIRED"; _write(data); raise ValueError("external change sudah kedaluwarsa")
        record["status"] = "EXECUTING"; record["approved_at"] = _now().isoformat(); _write(data)
    try:
        result = _execute(record)
    except Exception as exc:
        with exclusive(STATE_FILE):
            data = _read(); record = data[change_id.upper()]; record.update({"status": "FAILED", "error": type(exc).__name__, "error_detail": _redact(str(exc))}); _write(data)
        raise
    with exclusive(STATE_FILE):
        data = _read(); record = data[change_id.upper()]; record.update({"status": "SUCCEEDED", "result": result, "finished_at": _now().isoformat()}); _write(data)
    return record


def deny(change_id: str, requester_id: int | str) -> dict[str, Any]:
    with exclusive(STATE_FILE):
        record = get(change_id, requester_id)
        if record.get("status") != "AWAITING_APPROVAL":
            raise ValueError(f"change berstatus {record.get('status')}")
        data = _read(); record = data[change_id.upper()]; record["status"] = "DENIED"; record["denied_at"] = _now().isoformat(); _write(data)
    return record


def describe(record: dict[str, Any]) -> str:
    payload = json.dumps(record.get("payload", {}), ensure_ascii=False, separators=(", ", ": "))
    return f"**External change `{record['id']}`**\nService: `{record['service']}` · Action: `{record['action']}`\nPayload: `{payload[:900]}`\nRisiko: {record['risk']}\nStatus: **{record['status']}**\nBerlaku sampai: `{record['expires_at']}`\n\nApprove: `/external-approve {record['id']}`\nDeny: `/external-deny {record['id']}`"
