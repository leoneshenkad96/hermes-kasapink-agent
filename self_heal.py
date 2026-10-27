"""Safe, allowlisted Hermes self-diagnosis and repair helpers."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import local_runner


AUDIT_FILE = Path(os.getenv("HERMES_SELF_HEAL_AUDIT_FILE", "./data/self-heal.jsonl"))
TERMINAL_PRESETS = {
    "hermes_task": "Get-ScheduledTask -TaskName 'Hermes Kasapink Discord Bot' -ErrorAction SilentlyContinue | Select-Object TaskName,State | ConvertTo-Json -Compress",
    "processes": "Get-Process python,powershell,pwsh -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime | ConvertTo-Json -Compress",
    "network": "Test-NetConnection discord.com -Port 443 -InformationLevel Quiet",
    "dns_discord": "Resolve-DnsName discord.com -ErrorAction SilentlyContinue | Select-Object -First 5 Name,IPAddress | ConvertTo-Json -Compress",
    "system": "$os=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{computer=$env:COMPUTERNAME; python=$env:PYTHONHOME; memory_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,2)} | ConvertTo-Json -Compress",
    "chatgpt": "Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue | Select-Object Id,Responding,MainWindowHandle | ConvertTo-Json -Compress",
}


def _audit(event: str, **details: Any) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **details}
    with AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def diagnose(bot: Any) -> dict[str, Any]:
    """Read-only health snapshot; never raises to the Discord handler."""
    result: dict[str, Any] = {
        "runtime": {"os": platform.platform(), "python": platform.python_version()},
        "bot": {"ready": bool(bot.is_ready()), "latency_ms": round(bot.latency * 1000, 1) if bot.is_ready() else None},
    }
    for target in ("hermes", "network", "disk", "chatgpt"):
        try:
            result[target] = local_runner.diagnose(target)
        except Exception as exc:
            result[target] = {"status": "ERROR", "error": type(exc).__name__}
    _audit("diagnose", ready=result["bot"]["ready"], latency_ms=result["bot"]["latency_ms"])
    return result


def repair() -> dict[str, Any]:
    """Only perform the existing, allowlisted Hermes restart action."""
    result = local_runner.restart_hermes()
    _audit("repair_restart_hermes", status=result.get("status"))
    return result


def terminal_preset(name: str) -> dict[str, Any]:
    """Run one fixed PowerShell diagnostic; arbitrary commands are rejected."""
    key = name.strip().lower()
    script = TERMINAL_PRESETS.get(key)
    if script is None:
        raise ValueError("preset terminal tidak dikenal; gunakan: " + ", ".join(sorted(TERMINAL_PRESETS)))
    output = local_runner._powershell(script, timeout=15)
    _audit("terminal_preset", preset=key)
    return {"preset": key, "output": output[:5000]}
