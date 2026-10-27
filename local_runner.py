"""Allowlisted local laptop diagnostics and tightly scoped actions."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from file_lock import exclusive


KILL_SWITCH = Path(os.getenv("HERMES_LOCAL_KILL_SWITCH", "./data/LOCAL_ACTIONS_DISABLED"))
CHATGPT_TASK_NAME = "ChatGPT At User Logon"
CHATGPT_PROCESS_NAME = "ChatGPT"
CHATGPT_APP_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
ALLOWED = {"system", "disk", "processes", "network", "wireguard", "hermes", "chatgpt"}
APPROVED_ACTIONS = {"start_chatgpt", "restart_chatgpt", "restart_hermes", "restart_laptop"}
RESTART_NOTIFICATION_PATH = Path(os.getenv("HERMES_RESTART_NOTIFICATION_FILE", "./data/restart-notifications.json"))
RESTART_NOTIFICATION_TTL_SECONDS = 180


def _read_restart_notifications() -> list[dict[str, Any]]:
    try:
        data = json.loads(RESTART_NOTIFICATION_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_restart_notifications(items: list[dict[str, Any]]) -> None:
    RESTART_NOTIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESTART_NOTIFICATION_PATH.with_suffix(RESTART_NOTIFICATION_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(items[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(RESTART_NOTIFICATION_PATH)


def _record_restart_notification(action: str) -> str:
    now = time.time()
    with exclusive(RESTART_NOTIFICATION_PATH):
        items = _read_restart_notifications()
        for item in reversed(items):
            if item.get("action") == action and item.get("status") == "PENDING" and now - float(item.get("requested_at", 0)) < 30:
                return str(item.get("id"))
        notification_id = f"RST-{int(now * 1000):X}"
        items.append({
            "id": notification_id,
            "action": action,
            "status": "PENDING",
            "requested_at": now,
            "expires_at": now + RESTART_NOTIFICATION_TTL_SECONDS,
        })
        _write_restart_notifications(items)
        return notification_id


def pending_restart_notifications() -> list[dict[str, Any]]:
    with exclusive(RESTART_NOTIFICATION_PATH):
        items = _read_restart_notifications()
        return [dict(item) for item in items if item.get("status") == "PENDING"]


def mark_restart_notification(notification_id: str, status: str, detail: str = "") -> None:
    with exclusive(RESTART_NOTIFICATION_PATH):
        items = _read_restart_notifications()
        for item in items:
            if item.get("id") == notification_id:
                item["status"] = status
                item["finished_at"] = time.time()
                if detail:
                    item["detail"] = detail[:240]
                break
        _write_restart_notifications(items)


def enabled() -> bool:
    return os.getenv("HERMES_LOCAL_ACTIONS_ENABLED", "true").lower() in {"1", "true", "yes", "on"} and not KILL_SWITCH.exists()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"(?i)(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    return value


def _powershell(script: str, timeout: int = 15) -> str:
    if platform.system() != "Windows":
        return "Windows-only diagnostic unavailable on this host."
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    output = (completed.stdout or completed.stderr).strip()
    return output[:6000] if output else "No data."


def _task_state() -> str:
    return _powershell("(Get-ScheduledTask -TaskName 'Hermes Kasapink Discord Bot' -ErrorAction SilentlyContinue).State")


def _scheduled_task_status(task_name: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        return {"present": False, "state": "NOT_FOUND"}
    match = re.search(r"(?im)^\s*(?:Status|Task State):\s*(.+?)\s*$", output)
    return {"present": True, "state": match.group(1).strip() if match else "UNKNOWN"}


def _chatgpt_processes() -> list[dict[str, Any]]:
    raw = _powershell(
        "Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue | "
        "Select-Object Id,Responding,MainWindowHandle | ConvertTo-Json -Compress"
    )
    if not raw or raw == "No data.":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    return parsed if isinstance(parsed, list) else []


def chatgpt_status() -> dict[str, Any]:
    """Read ChatGPT desktop process and its existing startup task."""
    if platform.system() != "Windows":
        return {"status": "UNAVAILABLE", "reason": "ChatGPT desktop adapter hanya tersedia di Windows."}
    processes = _chatgpt_processes()
    responding = sum(1 for process in processes if process.get("Responding") is True)
    if not processes:
        state = "NOT_RUNNING"
    elif responding != len(processes):
        state = "UNRESPONSIVE"
    else:
        state = "RUNNING"
    task = _scheduled_task_status(CHATGPT_TASK_NAME)
    return {
        "status": state,
        "process_count": len(processes),
        "responding_count": responding,
        "startup_task": task,
        "startup_task_name": CHATGPT_TASK_NAME,
    }


def _run_chatgpt_startup_task() -> None:
    task = _scheduled_task_status(CHATGPT_TASK_NAME)
    if not task.get("present"):
        raise RuntimeError(f"Task `{CHATGPT_TASK_NAME}` tidak ditemukan")
    completed = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", CHATGPT_TASK_NAME],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Windows menolak menjalankan task startup ChatGPT")


def start_chatgpt(*, record_notification: bool = True) -> dict[str, Any]:
    """Start ChatGPT through the existing idempotent logon task."""
    if not enabled():
        raise RuntimeError("local actions disabled oleh kill switch")
    before = chatgpt_status()
    if before.get("status") == "RUNNING":
        return {"status": "ALREADY_RUNNING", "before": before, "task": CHATGPT_TASK_NAME}
    if record_notification:
        _record_restart_notification("start_chatgpt")
    _run_chatgpt_startup_task()
    deadline = time.monotonic() + 30
    after = before
    while time.monotonic() < deadline:
        time.sleep(2)
        after = chatgpt_status()
        if after.get("status") == "RUNNING":
            return {"status": "STARTED", "before": before, "after": after, "task": CHATGPT_TASK_NAME}
    return {"status": "START_REQUESTED", "verified": False, "before": before, "after": after, "task": CHATGPT_TASK_NAME}


def restart_chatgpt() -> dict[str, Any]:
    """Restart only ChatGPT.exe, then relaunch through the known startup task."""
    if not enabled():
        raise RuntimeError("local actions disabled oleh kill switch")
    before = chatgpt_status()
    notification_id = _record_restart_notification("restart_chatgpt")
    stopped = subprocess.run(
        ["taskkill.exe", "/F", "/IM", "ChatGPT.exe"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    # ERRORLEVEL 128 means no matching process; that is safe for a start/recovery request.
    if stopped.returncode not in (0, 128):
        raise RuntimeError("Windows menolak penutupan proses ChatGPT")
    time.sleep(2)
    result = start_chatgpt(record_notification=False)
    result["action"] = "restart_chatgpt"
    result["before"] = before
    result["restart_notification_id"] = notification_id
    return result


def diagnose(target: str = "system") -> dict[str, Any]:
    if not enabled():
        return {"status": "DISABLED", "reason": "local kill switch atau HERMES_LOCAL_ACTIONS_ENABLED=false"}
    target = target.lower().strip()
    if target not in ALLOWED:
        raise ValueError(f"target hanya boleh: {', '.join(sorted(ALLOWED))}")
    if target == "system":
        cpu = _powershell("$cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; $os=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{cpu_usage_percent=[math]::Round($cpu,1); memory_total_gb=[math]::Round($os.TotalVisibleMemorySize/1MB,2); memory_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,2)} | ConvertTo-Json -Compress")
        try:
            usage = json.loads(cpu)
        except json.JSONDecodeError:
            usage = cpu
        return {"status": "OK", "os": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count(), "usage": _redact(usage)}
    if target == "disk":
        usage = shutil.disk_usage(Path.cwd())
        return {"status": "OK", "path": str(Path.cwd()), "free_gb": round(usage.free / 1024**3, 2), "total_gb": round(usage.total / 1024**3, 2)}
    if target == "processes":
        return {"status": "OK", "processes": _redact(_powershell("Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress"))}
    if target == "network":
        return {"status": "OK", "adapters": _redact(_powershell("Get-NetAdapter | Select-Object Name,Status,LinkSpeed,MacAddress | ConvertTo-Json -Compress"))}
    if target == "wireguard":
        return {"status": "OK", "wireguard": _redact(_powershell("Get-Service | Where-Object {$_.Name -match 'wireguard'} | Select-Object Name,Status,StartType | ConvertTo-Json -Compress"))}
    if target == "chatgpt":
        return chatgpt_status()
    return {"status": "OK", "task_scheduler": _task_state(), "kill_switch": KILL_SWITCH.exists()}


def restart_hermes() -> dict[str, Any]:
    if not enabled():
        raise RuntimeError("local actions disabled oleh kill switch")
    task_name = "Hermes Kasapink Discord Bot"
    query = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
        capture_output=True, timeout=15, check=False,
    )
    if query.returncode != 0:
        raise RuntimeError("Task Scheduler tidak bisa membaca konfigurasi Hermes")
    try:
        xml_text = query.stdout.decode("utf-8-sig")
        xml_text = re.sub(r"^\s*<\?xml[^?]*\?>", "", xml_text, count=1)
        root = ET.fromstring(xml_text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise RuntimeError("Konfigurasi Task Scheduler Hermes tidak terbaca") from exc
    policy = next(
        (node.text for node in root.iter() if node.tag.endswith("MultipleInstancesPolicy")),
        None,
    )
    if policy not in {"IgnoreNew", "Queue"}:
        raise RuntimeError(
            f"restart Hermes tidak aman: kebijakan task saat ini `{policy or 'unknown'}`. "
            "Gunakan `IgnoreNew` atau `Queue` agar instance ganda tidak dibuat."
        )
    state_query = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    state_text = (state_query.stdout or "") + (state_query.stderr or "")
    running = bool(re.search(r"(?im)^\s*(?:Task State|Status):\s*Running\s*$", state_text))
    notification_id = _record_restart_notification("restart_hermes")
    if running:
        # Run the stop/start sequence outside the current Scheduled Task. If
        # this task stopped itself synchronously, the current Python process
        # could die before the start command executes.
        script = (
            "$task='Hermes Kasapink Discord Bot'; Start-Sleep -Seconds 2; "
            "Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; "
            "Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'python.exe' -and $_.CommandLine -like '*hermes-kasapink-agent*discord_bot.py*'} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            "Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue"
        )
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return {"status": "START_REQUESTED", "task": task_name, "policy": policy, "restart_notification_id": notification_id}
    completed = subprocess.run(
        ["schtasks.exe", "/Run", "/TN", task_name],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Task Scheduler menolak start Hermes")
    return {"status": "START_REQUESTED", "task": task_name, "policy": policy, "restart_notification_id": notification_id}


def restart_laptop() -> dict[str, Any]:
    """Schedule a fixed 20-second Windows restart; never accept command text."""
    if not enabled():
        raise RuntimeError("local actions disabled oleh kill switch")
    completed = subprocess.run(
        [
            "shutdown.exe", "/r", "/t", "20", "/d", "p:4:1",
            "/c", "Hermes approved laptop restart",
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Windows menolak penjadwalan restart")
    return {"status": "RESTART_SCHEDULED", "delay_seconds": 20}


def execute_action(action: str) -> dict[str, Any]:
    if action == "start_chatgpt":
        return start_chatgpt()
    if action == "restart_chatgpt":
        return restart_chatgpt()
    if action == "restart_hermes":
        return restart_hermes()
    if action == "restart_laptop":
        return restart_laptop()
    raise ValueError("local action belum di-allowlist")


def render(result: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(_redact(result), ensure_ascii=False, indent=2)[:1700] + "\n```"
