"""Deterministic anomaly rules; AI may explain results but cannot create facts."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import monitor


STOCK_DROP_RATIO = float(os.getenv("HERMES_STOCK_DROP_RATIO", "0.5"))
PRICE_CHANGE_RATIO = float(os.getenv("HERMES_PRICE_CHANGE_RATIO", "0.2"))
ROUTER_CPU_HIGH_PERCENT = float(os.getenv("HERMES_ROUTER_CPU_HIGH_PERCENT", "90"))
ROUTER_MEMORY_LOW_PERCENT = float(os.getenv("HERMES_ROUTER_MEMORY_LOW_PERCENT", "10"))


def _event(rule_id: str, system: str, severity: str, message: str, before: Any = None, after: Any = None) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(), "rule_id": rule_id,
        "system": system, "severity": severity, "message": message,
        "before": before, "after": after,
    }


def _by_id(items: Any) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id") is not None}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _resource(router: dict[str, Any]) -> dict[str, Any]:
    rows = router.get("snapshot", {}).get("resource", [])
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}


def detect(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    if not previous:
        return []
    found: list[dict[str, Any]] = []
    old_systems = previous.get("systems", {})
    new_systems = current.get("systems", {})
    old_erp = old_systems.get("kasapink", {}).get("state", {})
    new_erp_data = new_systems.get("kasapink", {})
    new_erp = new_erp_data.get("state", {}) if new_erp_data.get("status") == "OK" else {}
    old_ingredients = _by_id(old_erp.get("ingredients", []))
    new_ingredients = _by_id(new_erp.get("ingredients", []))
    for item_id, after in new_ingredients.items():
        before = old_ingredients.get(item_id)
        if not before:
            continue
        old_stock, new_stock = _number(before.get("stock")), _number(after.get("stock"))
        if old_stock is None or new_stock is None:
            continue
        if new_stock < 0 and old_stock >= 0:
            found.append(_event("ERP_NEGATIVE_STOCK", "kasapink", "CRITICAL", f"Stok {after.get('name', item_id)} menjadi negatif: {old_stock} → {new_stock} {after.get('unit', '')}.", before, after))
        elif old_stock > 0 and new_stock <= old_stock * (1 - STOCK_DROP_RATIO):
            found.append(_event("ERP_STOCK_DROP", "kasapink", "HIGH", f"Stok {after.get('name', item_id)} turun tajam: {old_stock} → {new_stock} {after.get('unit', '')}.", before, after))
        old_price, new_price = _number(before.get("averageCost")) or 0, _number(after.get("averageCost")) or 0
        if old_price > 0 and abs(new_price - old_price) / old_price >= PRICE_CHANGE_RATIO:
            found.append(_event("ERP_COST_CHANGE", "kasapink", "MEDIUM", f"Biaya rata-rata {after.get('name', item_id)} berubah: {old_price} → {new_price}.", before, after))

    for system in ("kasapink", "core_gateway", "lan_controller"):
        old_data = previous.get("systems", {}).get(system, {})
        new_data = current.get("systems", {}).get(system, {})
        if old_data.get("status") == "OK" and new_data.get("status") != "OK":
            label = "ERP" if system == "kasapink" else system
            found.append(_event("SYSTEM_UNAVAILABLE", system, "CRITICAL", f"{label} berubah dari OK menjadi {new_data.get('status', 'UNKNOWN')}.", old_data, new_data))
            continue
        if system not in {"core_gateway", "lan_controller"} or new_data.get("status") != "OK":
            continue
        resource = _resource(new_data)
        old_resource = _resource(old_systems.get(system, {}))
        cpu = _number(resource.get("cpu-load"))
        old_cpu = _number(old_resource.get("cpu-load"))
        if cpu is not None and cpu >= ROUTER_CPU_HIGH_PERCENT and (old_cpu is None or old_cpu < ROUTER_CPU_HIGH_PERCENT):
            found.append(_event("ROUTER_CPU_HIGH", system, "HIGH", f"CPU {system} melewati ambang: {old_cpu if old_cpu is not None else '?'}% → {cpu:.0f}% (ambang {ROUTER_CPU_HIGH_PERCENT:.0f}%).", {"cpu-load": old_resource.get("cpu-load")}, {"cpu-load": resource.get("cpu-load")}))
        free_memory = _number(resource.get("free-memory"))
        total_memory = _number(resource.get("total-memory"))
        old_free = _number(old_resource.get("free-memory"))
        old_total = _number(old_resource.get("total-memory"))
        if free_memory is not None and total_memory and total_memory > 0:
            free_pct = free_memory / total_memory * 100
            old_free_pct = (old_free / old_total * 100) if old_free is not None and old_total else None
            if free_pct <= ROUTER_MEMORY_LOW_PERCENT and (old_free_pct is None or old_free_pct > ROUTER_MEMORY_LOW_PERCENT):
                found.append(_event("ROUTER_MEMORY_LOW", system, "HIGH", f"RAM bebas turun melewati ambang: {old_free_pct if old_free_pct is not None else '?'}% → {free_pct:.1f}% (ambang {ROUTER_MEMORY_LOW_PERCENT:.0f}%).", {"free-memory": old_free, "total-memory": old_total}, {"free-memory": free_memory, "total-memory": total_memory}))
        old_data = previous.get("systems", {}).get(system, {})
        if old_data.get("status") == "OK" and monitor.stable_hash(old_data.get("snapshot", {})) != monitor.stable_hash(new_data.get("snapshot", {})):
            found.append(_event("ROUTER_CONFIG_OR_STATE_CHANGE", system, "MEDIUM", f"Snapshot read-only {system} berubah; nilai before/after tersedia untuk ditinjau.", {"hash": monitor.stable_hash(old_data.get("snapshot", {}))}, {"hash": monitor.stable_hash(new_data.get("snapshot", {}))}))
    return found


def render(anomalies: list[dict[str, Any]]) -> str:
    if not anomalies:
        return "Tidak ada anomali rule-based pada snapshot ini."
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    lines = ["**Hermes Anomaly Report**"]
    for item in sorted(anomalies, key=lambda x: order.get(x["severity"], 9)):
        lines.append(f"- **{item['severity']}** `{item['system']}` — {item['message']}")
    return "\n".join(lines)
