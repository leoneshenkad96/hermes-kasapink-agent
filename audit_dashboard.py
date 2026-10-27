"""Generate a local, dependency-free HTML audit dashboard."""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def generate(db_path: str = "./data/history.db", output_path: str = "./data/audit-dashboard.html") -> str:
    db = Path(db_path); output = Path(output_path)
    if not db.exists():
        raise RuntimeError("Belum ada history database")
    with sqlite3.connect(db) as connection:
        snapshots = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        anomalies = connection.execute("SELECT COUNT(*) FROM anomaly_events").fetchone()[0]
        severe = connection.execute("SELECT COUNT(*) FROM anomaly_events WHERE severity IN ('CRITICAL','HIGH')").fetchone()[0]
        rows = connection.execute("SELECT created_at,severity,system,rule_id,message FROM anomaly_events ORDER BY id DESC LIMIT 50").fetchall()
    table = "".join(f"<tr><td>{html.escape(str(a))}</td><td><b>{html.escape(str(b))}</b></td><td>{html.escape(str(c))}</td><td>{html.escape(str(d))}</td><td>{html.escape(str(e))}</td></tr>" for a,b,c,d,e in rows)
    source_time = datetime.now(timezone.utc).isoformat()
    content = f"""<!doctype html><meta charset='utf-8'><title>Hermes Audit Dashboard</title>
<style>body{{font:15px system-ui;background:#f7f5f0;color:#24221f;max-width:1100px;margin:40px auto;padding:0 20px}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{background:white;border:1px solid #ddd2bd;border-radius:10px;padding:18px;min-width:160px}}strong{{font-size:28px;display:block}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:10px;border-bottom:1px solid #eee;text-align:left}}th{{background:#eee6d7}}</style>
<h1>Hermes Audit Dashboard</h1><p>Dibuat: {html.escape(source_time)}</p>
<div class='cards'><div class='card'>Snapshot<strong>{snapshots}</strong></div><div class='card'>Anomaly<strong>{anomalies}</strong></div><div class='card'>High/Critical<strong>{severe}</strong></div></div>
<h2>Anomaly terbaru</h2><table><thead><tr><th>Waktu</th><th>Severity</th><th>Sistem</th><th>Rule</th><th>Pesan</th></tr></thead><tbody>{table or '<tr><td colspan=5>Belum ada anomaly</td></tr>'}</tbody></table>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return str(output)
