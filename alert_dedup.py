"""Persistent, cross-process alert claiming for the Discord monitor."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from file_lock import exclusive


STATE_FILE = Path(os.getenv("HERMES_ALERT_DEDUP_FILE", "./data/alert-dedup.json"))
COOLDOWN_MINUTES = max(0, int(os.getenv("HERMES_ALERT_COOLDOWN_MINUTES", "180")))


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _read() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "claims": {}}
    if not isinstance(value, dict) or not isinstance(value.get("claims"), dict):
        return {"version": 1, "claims": {}}
    return value


def _write(value: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def claim(fingerprint: str, *, now: datetime | None = None, cooldown_minutes: int | None = None) -> bool:
    """Atomically claim an alert; duplicate claims within cooldown are suppressed."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cooldown = COOLDOWN_MINUTES if cooldown_minutes is None else max(0, int(cooldown_minutes))
    with exclusive(STATE_FILE):
        state = _read()
        claims = state["claims"]
        previous = claims.get(fingerprint)
        previous_at = _parse_time(previous.get("claimed_at")) if isinstance(previous, dict) else None
        if previous_at and cooldown and current - previous_at < timedelta(minutes=cooldown):
            return False
        claims[fingerprint] = {"claimed_at": current.isoformat()}
        cutoff = current - timedelta(minutes=max(cooldown * 2, 1440))
        state["claims"] = {
            key: record
            for key, record in claims.items()
            if isinstance(record, dict) and (_parse_time(record.get("claimed_at")) or current) >= cutoff
        }
        state["updated_at"] = current.isoformat()
        _write(state)
        return True


def fingerprint(event: str, snapshot: dict[str, Any] | None = None,
                anomalies: list[dict[str, Any]] | None = None,
                changes: list[str] | None = None) -> str:
    """Build a stable condition fingerprint without timestamps or secret values."""
    systems = (snapshot or {}).get("systems", {})
    stable_systems = {
        name: {key: data.get(key) for key in ("status", "error")}
        for name, data in sorted(systems.items())
        if isinstance(data, dict)
    }
    stable_anomalies = [
        {key: item.get(key) for key in ("rule_id", "system", "severity", "message")}
        for item in (anomalies or [])
    ]
    payload = {"event": event, "systems": stable_systems, "anomalies": stable_anomalies,
               "changes": list(changes or [])}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
