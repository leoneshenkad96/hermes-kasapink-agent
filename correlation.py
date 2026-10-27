"""Cross-system correlation for same-cycle ERP and network anomalies."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def correlate(anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    systems = {item.get("system") for item in anomalies}
    if "kasapink" not in systems or not systems.intersection({"core_gateway", "lan_controller"}):
        return []
    return [{
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rule_id": "CROSS_SYSTEM_CHANGE",
        "system": "erp_network",
        "severity": "HIGH",
        "message": "Perubahan ERP dan jaringan terjadi pada siklus pemeriksaan yang sama; korelasi perlu ditinjau sebelum menyimpulkan penyebab.",
        "before": {"systems": sorted(systems)},
        "after": {"anomaly_count": len(anomalies)},
    }]
