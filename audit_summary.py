"""Compact local audit summary for Discord and engineering handoff."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def render(db_path: str = "./data/history.db") -> str:
    path = Path(db_path)
    if not path.exists():
        return "Belum ada history database."
    with sqlite3.connect(path) as db:
        snapshots = db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        anomalies = db.execute("SELECT COUNT(*) FROM anomaly_events").fetchone()[0]
        severe = db.execute("SELECT COUNT(*) FROM anomaly_events WHERE severity IN ('CRITICAL','HIGH')").fetchone()[0]
        latest = db.execute("SELECT captured_at,event FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    latest_text = f"{latest[0]} ({latest[1]})" if latest else "belum ada"
    return f"**Hermes Audit**\n- Snapshot tersimpan: **{snapshots}**\n- Anomaly tercatat: **{anomalies}**\n- Anomaly HIGH/CRITICAL: **{severe}**\n- Snapshot terakhir: `{latest_text}`"
