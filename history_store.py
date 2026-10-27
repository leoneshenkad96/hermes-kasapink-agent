"""Local SQLite history for non-secret Hermes snapshots and anomaly events."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path(os.getenv("HERMES_HISTORY_DB", "./data/history.db"))
RETENTION_DAYS = max(7, int(os.getenv("HERMES_HISTORY_RETENTION_DAYS", "90")))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            event TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_captured_at ON snapshots(captured_at);
        CREATE TABLE IF NOT EXISTS anomaly_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            severity TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            system TEXT NOT NULL,
            message TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
        );
        CREATE INDEX IF NOT EXISTS idx_anomalies_snapshot_id ON anomaly_events(snapshot_id);
        """
    )
    return connection


def append_snapshot(captured_at: str, event: str, snapshot_hash: str, snapshot: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous = json.loads(row[0]) if row else None
        cursor = connection.execute(
            "INSERT INTO snapshots(captured_at,event,snapshot_hash,snapshot_json) VALUES(?,?,?,?)",
            (captured_at, event, snapshot_hash, json.dumps(snapshot, ensure_ascii=False)),
        )
        connection.execute(
            "DELETE FROM snapshots WHERE julianday(captured_at) < julianday('now', ?)",
            (f"-{RETENTION_DAYS} days",),
        )
        # Keep anomaly retention aligned with snapshots and remove orphaned rows.
        connection.execute(
            "DELETE FROM anomaly_events WHERE julianday(created_at) < julianday('now', ?)",
            (f"-{RETENTION_DAYS} days",),
        )
        connection.execute(
            "DELETE FROM anomaly_events WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
        )
        return int(cursor.lastrowid), previous


def append_anomalies(snapshot_id: int, anomalies: list[dict[str, Any]]) -> None:
    if not anomalies:
        return
    with _connect() as connection:
        connection.executemany(
            """INSERT INTO anomaly_events
            (snapshot_id,created_at,severity,rule_id,system,message,before_json,after_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            [(
                snapshot_id, item["created_at"], item["severity"], item["rule_id"], item["system"],
                item["message"], json.dumps(item.get("before"), ensure_ascii=False),
                json.dumps(item.get("after"), ensure_ascii=False),
            ) for item in anomalies],
        )


def latest_anomalies(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT created_at,severity,rule_id,system,message FROM anomaly_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(zip(("created_at", "severity", "rule_id", "system", "message"), row)) for row in rows]


def recent_anomalies(since: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return a bounded, newest-first anomaly digest since an ISO timestamp."""
    with _connect() as connection:
        rows = connection.execute(
            """SELECT created_at,severity,rule_id,system,message FROM anomaly_events
               WHERE julianday(created_at) >= julianday(?) ORDER BY id DESC LIMIT ?""",
            (since, max(1, min(int(limit), 50))),
        ).fetchall()
    return [dict(zip(("created_at", "severity", "rule_id", "system", "message"), row)) for row in rows]
