"""Render conservative, read-only MikroTik WAN observations."""

from __future__ import annotations

from typing import Any


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def render_isp_status(system: dict[str, Any] | None) -> str:
    """Summarize ISP-named interfaces and routes without implying Internet reachability."""
    if not isinstance(system, dict) or system.get("status") != "OK":
        status = system.get("status", "UNKNOWN") if isinstance(system, dict) else "UNKNOWN"
        return f"🌐 Core Gateway belum bisa dibaca (status `{status}`); status tiap ISP belum dapat dipastikan."

    snapshot = system.get("snapshot", {})
    interfaces = snapshot.get("interfaces", []) if isinstance(snapshot, dict) else []
    routes = snapshot.get("routes", []) if isinstance(snapshot, dict) else []
    if not isinstance(interfaces, list):
        interfaces = []
    if not isinstance(routes, list):
        routes = []

    wan_interfaces = [
        row for row in interfaces
        if isinstance(row, dict)
        and "isp" in str(row.get("name", "") or row.get("comment", "")).lower()
    ]
    captured_at = snapshot.get("captured_at") if isinstance(snapshot, dict) else None
    lines = ["🌐 **Status ISP Core Gateway**"]
    if captured_at:
        lines.append(f"Snapshot: `{captured_at}`")
    if not wan_interfaces:
        return "\n".join(lines + [
            "Belum ada interface bernama/berlabel ISP di data RouterOS, jadi Hermes tidak bisa memetakan ISP satu per satu.",
            "Ping dan packet loss juga belum diukur.",
        ])

    for interface in wan_interfaces:
        name = str(interface.get("name", "") or "ISP (nama tidak tersedia)")
        label = name.split("ISP-", 1)[-1] if "ISP-" in name else name
        running = _as_bool(interface.get("running"))
        disabled = _as_bool(interface.get("disabled"))
        if disabled is True:
            link_text = "⏸️ disabled"
        elif running is True:
            link_text = "🟢 link UP"
        elif running is False:
            link_text = "🔴 link DOWN"
        else:
            link_text = "⚪ status link tidak dikirim"

        matched_routes = []
        for route in routes:
            if not isinstance(route, dict) or route.get("dst-address") != "0.0.0.0/0":
                continue
            immediate_gw = str(route.get("immediate-gw", "")).lower()
            table = str(route.get("routing-table", "")).lower()
            if name.lower() in immediate_gw or label.lower() in table:
                matched_routes.append(route)

        # Prefer the dedicated per-ISP policy table over the shared failover
        # table, whose backup routes may be inactive by design.
        dedicated_routes = [
            route for route in matched_routes
            if str(route.get("routing-table", "")).lower() not in {"", "main"}
        ]
        if dedicated_routes:
            matched_routes = dedicated_routes

        route_parts = []
        for route in matched_routes:
            table = str(route.get("routing-table", "table tidak diketahui"))
            active = _as_bool(route.get("active"))
            if active is True:
                ping_check = str(route.get("check-gateway", "")).lower() == "ping"
                state = "✅ aktif · check-gateway ping" if ping_check else "✅ aktif"
            elif active is False:
                state = "⛔ tidak aktif"
            else:
                state = "⚪ flag aktif tidak tersedia"
            route_parts.append(f"`{table}` {state}")
        route_text = "; ".join(route_parts) if route_parts else "rute default tidak terpetakan"
        lines.append(f"- **{label}** — {link_text}; route: {route_text}; ping/loss: belum diukur.")

    lines.append("ℹ️ `check-gateway=ping` adalah pemeriksaan reachability oleh RouterOS, bukan laporan latency/packet-loss terukur. Link UP sendiri hanya menandakan interface terhubung.")
    return "\n".join(lines)
