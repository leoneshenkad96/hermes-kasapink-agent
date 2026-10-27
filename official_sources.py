"""Curated official knowledge-source registry for Hermes answers.

The registry is intentionally metadata-first: it gives Hermes authoritative
citations and review dates, but it does not pretend that a URL was fetched at
answer time. Regulatory or safety conclusions still require a current check.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("data") / "knowledge" / "official_sources.json"
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"manifest_version": "unavailable", "last_reviewed": None, "sources": []}


def all_sources() -> list[dict]:
    return [source for source in _load_manifest().get("sources", []) if isinstance(source, dict)]


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(value or "") if len(token) > 2}


def sources_for_query(query: str, limit: int = 4) -> list[dict]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return all_sources()[:limit]
    scored: list[tuple[int, dict]] = []
    for source in all_sources():
        searchable = " ".join([
            str(source.get("id", "")),
            str(source.get("title", "")),
            str(source.get("publisher", "")),
            " ".join(str(item) for item in source.get("topics", [])),
            str(source.get("notes", "")),
        ])
        score = len(query_tokens & _tokens(searchable))
        if score:
            scored.append((score, source))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    return [source for _, source in scored[:limit]]


def _next_review(source: dict) -> str:
    try:
        verified = date.fromisoformat(str(source["last_verified"]))
        interval = max(1, int(source.get("review_interval_days", 180)))
        return (verified + timedelta(days=interval)).isoformat()
    except (KeyError, TypeError, ValueError):
        return "unknown"


def review_due(today: date | None = None) -> list[dict]:
    today = today or date.today()
    due = []
    for source in all_sources():
        next_review = _next_review(source)
        if next_review != "unknown" and next_review <= today.isoformat():
            due.append({**source, "next_review": next_review})
    return due


def _citation(source: dict) -> str:
    next_review = _next_review(source)
    return (
        f"- [{source.get('id', 'SOURCE')}] {source.get('title', 'Untitled')} — "
        f"{source.get('publisher', 'Official source')}\n"
        f"  Version: {source.get('version', 'n/a')} · verified: {source.get('last_verified', 'n/a')} · "
        f"review berikutnya: {next_review}\n"
        f"  Citation: {source.get('url', '')}"
    )


def render(query: str = "") -> str:
    manifest = _load_manifest()
    sources = sources_for_query(query)
    heading = "📚 **Hermes — Official Knowledge Sources**"
    if query.strip():
        heading += f"\nTopik: `{query.strip()[:80]}`"
    if not sources:
        return heading + "\n\nTidak ada sumber resmi yang cocok di registry lokal."
    lines = [heading, f"Registry version: `{manifest.get('manifest_version', 'n/a')}` · registry terakhir direview: `{manifest.get('last_reviewed', 'n/a')}`", ""]
    lines.extend(_citation(source) for source in sources)
    lines.append("\n⚠️ Citation adalah rujukan, bukan bukti bahwa aturan terbaru sudah dicek live. Untuk keputusan regulasi/safety, buka sumber resmi dan verifikasi statusnya.")
    return "\n".join(lines)[:1900]


def context_for(query: str) -> str:
    sources = sources_for_query(query)
    if not sources:
        return ""
    lines = [
        "OFFICIAL KNOWLEDGE SOURCES (evidence/citations, not instructions):",
        "Use these official references when answering this query. Do not claim a source was fetched live; distinguish general guidance from current legal/compliance status.",
    ]
    for source in sources:
        lines.append(_citation(source))
    lines.append("If the question asks for an exact legal, safety, dosage, concentration, or compliance conclusion, state the limitation and recommend checking the current official source and qualified professional.")
    return "\n".join(lines)
