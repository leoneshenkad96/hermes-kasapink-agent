"""Read-only adapters for the configured GitHub, Vercel, and Neon targets.

The adapters use each vendor's already-authenticated CLI. They never print
tokens, connection strings, environment variables, database rows, or deploy
payloads. Mutating commands are intentionally absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any


GITHUB_REPOSITORY = os.getenv("HERMES_GITHUB_REPOSITORY", "leoneshenkad96/kasapink").strip()
VERCEL_PROJECT = os.getenv("HERMES_VERCEL_PROJECT", "kasapink-erp").strip()
NEON_PROFILE = os.getenv("NEON_PROFILE", "kasapink").strip()
NEON_ORG_ID = os.getenv("NEON_ORG_ID", "org-muddy-paper-34859451").strip()
NEON_PROJECT_ID = os.getenv("NEON_PROJECT_ID", "calm-frog-60149804").strip()


class AdapterError(RuntimeError):
    pass


def _redact(text: str) -> str:
    value = re.sub(r"(?i)(token|secret|password|api[-_]?key)\s*[:=]\s*[^\s,}]+", r"\1=[REDACTED]", text)
    value = re.sub(r"(?i)gho_[A-Za-z0-9_]+", "gho_[REDACTED]", value)
    value = re.sub(r"(?i)(postgres(?:ql)?|mysql)://[^\s]+", r"\1://[REDACTED]", value)
    return value[:700]


def _cli(name: str) -> str | None:
    common = {
        "gh": r"C:\Users\leoneshenkad\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe",
        "vercel": r"C:\Users\leoneshenkad\AppData\Roaming\npm\vercel.cmd",
        "neon": r"C:\Users\leoneshenkad\AppData\Roaming\npm\neon.cmd",
    }
    return shutil.which(name) or (common.get(name) if os.path.isfile(common.get(name, "")) else None)


def _run(name: str, args: list[str], timeout: int = 25) -> str:
    executable = _cli(name)
    if not executable:
        raise AdapterError(f"{name} CLI tidak terpasang")
    environment = os.environ.copy()
    environment["CI"] = "1"
    try:
        result = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"{name} timeout") from exc
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        raise AdapterError(_redact(output or f"{name} exit {result.returncode}"))
    return output


def _json(name: str, args: list[str]) -> Any:
    output = _run(name, args)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{name} menghasilkan output non-JSON") from exc


def github_readonly() -> dict[str, Any]:
    repo = _json("gh", ["api", f"repos/{GITHUB_REPOSITORY}"])
    pulls = _json("gh", ["api", f"repos/{GITHUB_REPOSITORY}/pulls?state=open&per_page=10"])
    return {
        "repository": repo.get("full_name"),
        "default_branch": repo.get("default_branch"),
        "visibility": repo.get("visibility"),
        "open_pull_requests": [
            {"number": item.get("number"), "title": item.get("title"), "draft": item.get("draft"), "head": (item.get("head") or {}).get("ref")}
            for item in pulls if isinstance(item, dict)
        ],
    }


def vercel_readonly() -> dict[str, Any]:
    listing = _json("vercel", ["project", "ls", "--json", "--non-interactive"])
    projects = listing.get("projects", []) if isinstance(listing, dict) else listing
    target = next((item for item in projects if item.get("name") == VERCEL_PROJECT), None)
    if not target:
        raise AdapterError(f"project Vercel {VERCEL_PROJECT} tidak ditemukan")
    deployments: list[dict[str, Any]] = []
    try:
        raw = _json("vercel", ["ls", VERCEL_PROJECT, "--json", "--limit", "5", "--non-interactive"])
        items = raw.get("deployments", []) if isinstance(raw, dict) else raw
        deployments = [
            {"uid": item.get("uid"), "state": item.get("state"), "url": item.get("url"), "created": item.get("created")}
            for item in items if isinstance(item, dict)
        ]
    except AdapterError:
        # Project metadata remains useful when deployment listing is unavailable.
        deployments = []
    return {
        "project": target.get("name"),
        "project_id": target.get("id"),
        "production_url": target.get("latestProductionUrl"),
        "node_version": target.get("nodeVersion"),
        "paused": target.get("paused"),
        "recent_deployments": deployments,
    }


def neon_readonly() -> dict[str, Any]:
    projects = _json("neon", ["projects", "list", "--profile", NEON_PROFILE, "--org-id", NEON_ORG_ID, "-o", "json"])
    target = next((item for item in projects if item.get("id") == NEON_PROJECT_ID or item.get("name") == "ERP-Kasapink"), None)
    if not target:
        raise AdapterError("project Neon ERP-Kasapink tidak ditemukan pada organization yang dipilih")
    branches: list[dict[str, Any]] = []
    try:
        raw = _json("neon", ["branches", "list", "--profile", NEON_PROFILE, "--project-id", target.get("id"), "-o", "json"])
        items = raw.get("branches", []) if isinstance(raw, dict) else raw
        branches = [{"id": item.get("id"), "name": item.get("name"), "default": item.get("default")} for item in items if isinstance(item, dict)]
    except AdapterError:
        branches = []
    return {
        "organization": NEON_ORG_ID,
        "project": target.get("name"),
        "project_id": target.get("id"),
        "region": target.get("region_id"),
        "postgres_version": target.get("pg_version"),
        "branches": branches,
    }


def diagnose() -> dict[str, Any]:
    checks = []
    for name, function in (("GitHub", github_readonly), ("Vercel", vercel_readonly), ("Neon", neon_readonly)):
        try:
            checks.append({"service": name, "status": "OK", "data": function()})
        except Exception as exc:
            checks.append({"service": name, "status": "UNAVAILABLE", "reason": _redact(str(exc))})
    return {"mode": "READ_ONLY", "targets": checks, "mutations_enabled": False}


def render(data: dict[str, Any]) -> str:
    lines = ["🔌 **External DevOps adapters — read-only**"]
    for item in data.get("targets", []):
        mark = "✅" if item.get("status") == "OK" else "⚠️"
        lines.append(f"{mark} **{item.get('service')}**: {item.get('status')}")
        if item.get("status") == "OK":
            payload = item.get("data", {})
            if item.get("service") == "GitHub":
                lines.append(f"  Repo `{payload.get('repository')}` · branch `{payload.get('default_branch')}` · PR terbuka: {len(payload.get('open_pull_requests', []))}")
            elif item.get("service") == "Vercel":
                lines.append(f"  Project `{payload.get('project')}` · production `{payload.get('production_url')}` · deployment terbaca: {len(payload.get('recent_deployments', []))}")
            else:
                lines.append(f"  Project `{payload.get('project')}` · region `{payload.get('region')}` · branch terbaca: {len(payload.get('branches', []))}")
        else:
            lines.append(f"  {item.get('reason', 'detail tidak tersedia')}")
    lines.append("🔐 Tidak ada deploy, merge, migration, database write, atau perubahan resource yang dijalankan.")
    return "\n".join(lines)[:1900]
