"""Safe, read-only capability registry and local DevOps adapter probes.

This module deliberately does not execute arbitrary shell, deploy, database
write, router change, or credential-management commands. It reports what is
available and gives Hermes a staged plan. Mutating adapters can be added later
behind the existing approval and audit gates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any


CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("python", "Python debugging"),
    ("powershell", "PowerShell administration"),
    ("windows", "Windows internals"),
    ("linux", "Linux administration"),
    ("tcpip", "TCP/IP and subnetting"),
    ("routeros", "MikroTik RouterOS"),
    ("docker", "Docker and Compose"),
    ("kubernetes", "Kubernetes"),
    ("proxy", "Reverse proxy"),
    ("tls", "TLS and certificates"),
    ("database", "PostgreSQL and MySQL"),
    ("backup", "Backup and disaster recovery"),
    ("cicd", "CI/CD"),
    ("git", "Git workflow"),
    ("observability", "Observability and incident response"),
)


STAGES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "adapters": (
        "Tool adapter DevOps",
        ("deteksi tool lokal", "validasi koneksi read-only", "catat versi dan evidence", "kunci aksi mutasi dengan approval"),
        "Tool lokal atau konektor eksternal yang tidak tersedia tidak dianggap sehat.",
    ),
    "incident": (
        "Incident diagnosis",
        ("tetapkan dampak dan scope", "cek health/process/port/log/dependency", "korelasi waktu dan perubahan", "susun hipotesis serta recovery", "verifikasi setelah tindakan"),
        "Restart, perubahan firewall/routing, deploy, dan perubahan data harus disetujui.",
    ),
    "programmer": (
        "Programmer workflow",
        ("reproduksi bug", "baca traceback dan diff", "buat patch terkecil", "jalankan test/regression", "siapkan evidence dan rollback"),
        "Hermes tidak boleh menghapus perubahan user atau mengklaim test tanpa output.",
    ),
    "devops": (
        "DevOps workflow",
        ("plan dan precheck", "build/test/scan", "stage preview", "observasi health dan logs", "promote atau rollback"),
        "Production deploy, migration, secret rotation, dan rollback tetap approval-gated.",
    ),
}


_PROBES: dict[str, tuple[str, ...]] = {
    "Python": ("python", "--version"),
    "PowerShell": ("powershell", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"),
    "Docker": ("docker", "version", "--format", "{{.Server.Version}}"),
    "Compose": ("docker", "compose", "version"),
    "Kubernetes client": ("kubectl", "version", "--client=true", "-o", "json"),
    "Git": ("git", "--version"),
    "SSH": ("ssh", "-V"),
    "PostgreSQL client": ("psql", "--version"),
    "MySQL client": ("mysql", "--version"),
    "OpenSSL": ("openssl", "version"),
    "WSL": ("wsl", "--status"),
}


_COMMON_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "gh": (r"C:\Users\leoneshenkad\AppData\Local\Microsoft\WinGet\Links\gh.exe",),
    "vercel": (r"C:\Users\leoneshenkad\AppData\Roaming\npm\vercel.cmd",),
    "neon": (r"C:\Users\leoneshenkad\AppData\Roaming\npm\neon.cmd",),
    "psql": (r"C:\Program Files\PostgreSQL\17\bin\psql.exe", r"C:\Program Files\PostgreSQL\16\bin\psql.exe"),
    "mysql": (r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe", r"C:\Program Files\MySQL\MySQL Shell 8.0\bin\mysqlsh.exe"),
}


def _find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in _COMMON_EXECUTABLES.get(name, ()):
        if os.path.isfile(candidate):
            return candidate
    return None


def _probe(label: str, command: tuple[str, ...]) -> dict[str, Any]:
    executable = _find_executable(command[0])
    if not executable:
        return {"name": label, "status": "NOT_INSTALLED", "executable": command[0]}
    actual_command = (executable, *command[1:])
    try:
        completed = subprocess.run(
            list(actual_command),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"name": label, "status": "TIMEOUT", "executable": executable}
    output = (completed.stdout or completed.stderr or "").replace("\x00", "").strip().replace("\r", " ").replace("\n", " ")
    if not output:
        output = "tool ada, tetapi service/daemon tidak merespons" if completed.returncode else "ok"
    return {
        "name": label,
        "status": "OK" if completed.returncode == 0 else "UNAVAILABLE",
        "executable": executable,
        "detail": output[:220],
        "returncode": completed.returncode,
    }


def _cli_authenticated(name: str, executable: str | None) -> bool:
    """Check existing CLI login without printing or persisting credentials."""
    if not executable:
        return False
    commands = {
        "GitHub": ["auth", "status", "--hostname", "github.com", "--active"],
        "Vercel": ["whoami"],
        "Neon": ["profile", "list"],
    }
    args = commands.get(name)
    if not args:
        return False
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _integration(name: str, env_names: tuple[str, ...], cli_names: tuple[str, ...]) -> dict[str, Any]:
    configured = any(os.getenv(key, "").strip() for key in env_names)
    cli = next((_find_executable(item) for item in cli_names if _find_executable(item)), None)
    authenticated = configured or _cli_authenticated(name, cli)
    return {
        "service": name,
        "status": "AUTHENTICATED" if authenticated else "NOT_CONFIGURED",
        "cli": cli,
        "credential_values_exposed": False,
    }


def diagnose() -> dict[str, Any]:
    """Return a redacted, read-only inventory of local DevOps readiness."""
    probes = [_probe(label, command) for label, command in _PROBES.items()]
    integrations = [
        _integration("GitHub", ("GITHUB_TOKEN", "GH_TOKEN"), ("gh",)),
        _integration("Vercel", ("VERCEL_TOKEN",), ("vercel",)),
        _integration("Neon", ("NEON_API_KEY",), ("neon",)),
    ]
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": [{"id": key, "name": name, "status": "RUNBOOK_READY"} for key, name in CAPABILITIES],
        "probes": probes,
        "integrations": integrations,
        "safety": {
            "mode": "READ_ONLY",
            "approval_required_for": ["deploy", "promote", "rollback", "database_write", "router_change", "firewall_change", "secret_rotation", "service_restart"],
        },
    }


def workflow_plan(stage: str, request: str = "") -> dict[str, Any]:
    key = stage.strip().lower().replace("-", "_")
    aliases = {"tool_adapter": "adapters", "tool_adapters": "adapters", "incident_diagnosis": "incident", "programmer_workflow": "programmer", "devops_workflow": "devops"}
    key = aliases.get(key, key)
    if key not in STAGES:
        raise ValueError("stage harus adapters, incident, programmer, atau devops")
    title, steps, risk = STAGES[key]
    return {"stage": key, "title": title, "request": request[:500], "steps": list(steps), "risk": risk, "approval_required": key in {"incident", "devops"}}


def render_status(data: dict[str, Any]) -> str:
    lines = ["🛠️ **Hermes DevOps readiness — read-only**"]
    ready = sum(1 for item in data.get("capabilities", []) if item.get("status") == "RUNBOOK_READY")
    lines.append(f"📚 Runbook domain: **{ready}/{len(CAPABILITIES)} siap**")
    lines.append("\n**Adapter lokal**")
    for item in data.get("probes", []):
        mark = "✅" if item.get("status") == "OK" else "⚠️"
        detail = item.get("detail", item.get("status", "UNKNOWN"))
        lines.append(f"{mark} {item.get('name')}: `{detail[:90]}`")
    lines.append("\n**Koneksi eksternal**")
    for item in data.get("integrations", []):
        state = "✅ terautentikasi" if item.get("status") in {"CONFIGURED", "AUTHENTICATED"} else "🔒 belum terhubung"
        cli = " · CLI tersedia" if item.get("cli") else ""
        lines.append(f"- {item.get('service')}: {state}{cli}")
    lines.append("\n🔐 Mode saat ini: **read-only**; deploy, database write, router change, secret rotation, dan restart perlu approval.")
    return "\n".join(lines)[:1900]


def render_plan(plan: dict[str, Any]) -> str:
    lines = [f"🧭 **{plan['title']}**", f"Scope: {plan.get('request') or 'belum ditentukan'}", "", "**Tahapan**"]
    lines.extend(f"{index}. {step}" for index, step in enumerate(plan.get("steps", []), 1))
    lines.extend(["", f"⚠️ Risiko: {plan.get('risk')}", f"Approval: **{'ya' if plan.get('approval_required') else 'belum diperlukan untuk rencana read-only'}**"])
    return "\n".join(lines)[:1900]
