"""Local handoff queue between Hermes and a human/Codex engineering session."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_DIR = Path("./data/engineering/tasks")


def create(title: str, description: str, evidence: str = "") -> dict[str, Any]:
    task_id = "ENG-" + secrets.token_hex(4).upper()
    now = datetime.now(timezone.utc).isoformat()
    record = {"id": task_id, "status": "OPEN", "created_at": now, "title": title, "description": description, "evidence": evidence}
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    (TASK_DIR / f"{task_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (TASK_DIR / f"{task_id}.md").write_text(
        f"# {title}\n\n- ID: `{task_id}`\n- Status: `OPEN`\n- Created: `{now}`\n\n## Request\n\n{description}\n\n## Evidence\n\n{evidence or 'Belum ada bukti tambahan.'}\n\n## Safety\n\nJangan merge atau deploy tanpa review dan approval eksplisit.\n",
        encoding="utf-8",
    )
    return record


def list_open(limit: int = 10) -> list[dict[str, Any]]:
    if not TASK_DIR.exists():
        return []
    records = []
    for path in sorted(TASK_DIR.glob("ENG-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if item.get("status") == "OPEN":
            records.append(item)
    return records[:limit]
