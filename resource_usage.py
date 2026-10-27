"""Render observed memory usage only; never substitute capacity estimates."""
from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def laptop_line(result: dict[str, Any] | None) -> str:
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    total = _number(usage.get("memory_total_gb")) if isinstance(usage, dict) else None
    free = _number(usage.get("memory_free_gb")) if isinstance(usage, dict) else None
    if not result or result.get("status") != "OK" or total is None or free is None or total <= 0:
        return "💻 Laptop: data RAM tidak tersedia dari diagnostik saat ini."
    used = max(0.0, total - free)
    return f"💻 Laptop: **{used:.2f}/{total:.2f} GB terpakai ({used / total * 100:.1f}%)** · sisa {free:.2f} GB"


def router_line(label: str, system: dict[str, Any] | None) -> str:
    if not isinstance(system, dict) or system.get("status") != "OK":
        status = system.get("status", "UNKNOWN") if isinstance(system, dict) else "UNKNOWN"
        return f"🌐 {label}: data RAM tidak tersedia (status {status})."
    snapshot = system.get("snapshot", {})
    rows = snapshot.get("resource", []) if isinstance(snapshot, dict) else []
    resource = next((row for row in rows if isinstance(row, dict) and ("total-memory" in row or "free-memory" in row)), {}) if isinstance(rows, list) else {}
    total = _number(resource.get("total-memory"))
    free = _number(resource.get("free-memory"))
    if total is None or free is None or total <= 0:
        return f"🌐 {label}: router tersambung, tetapi data total/free RAM tidak dikirim API."
    used = max(0.0, total - free)
    to_mb = 1024 * 1024
    return f"🌐 {label}: **{used / to_mb:.0f}/{total / to_mb:.0f} MB terpakai ({used / total * 100:.1f}%)** · sisa {free / to_mb:.0f} MB"


def render(*, laptop: dict[str, Any] | None, snapshot: dict[str, Any] | None,
           can_see_laptop: bool, can_see_mikrotik: bool, scope: str = "all") -> str:
    lines = ["🧠 **Pemakaian RAM aktual**", "Angka berikut hasil pembacaan saat ini, bukan estimasi kebutuhan."]
    systems = snapshot.get("systems", {}) if isinstance(snapshot, dict) else {}
    if scope in {"all", "laptop"} and can_see_laptop:
        lines.append(laptop_line(laptop))
    elif scope in {"all", "laptop"}:
        lines.append("💻 Laptop: 🔒 hanya Henka yang bisa melihat diagnostik laptop.")
    if scope in {"all", "erp"}:
        lines.append("🖥️ Server ERP: **belum tersedia** — API ERP yang dikonfigurasi tidak menyediakan metrik RAM host; Hermes tidak menebak angkanya.")
    if scope in {"all", "mikrotik"} and can_see_mikrotik:
        lines.append(router_line("Core Gateway", systems.get("core_gateway")))
        lines.append(router_line("LAN Controller", systems.get("lan_controller")))
    elif scope in {"all", "mikrotik"}:
        lines.append("🌐 MikroTik: 🔒 hanya Henka yang bisa melihat metrik router.")
    captured = snapshot.get("captured_at") if isinstance(snapshot, dict) else None
    if captured:
        lines.append(f"Waktu snapshot jaringan: `{captured}`. Nilai router berasal dari pembacaan itu.")
    return "\n".join(lines)
