"""Local, read-only repository search for Hermes chat context."""

from __future__ import annotations

import re
from pathlib import Path
import os


ROOT = Path(os.getenv("KASAPINK_REPO_ROOT", str(Path(__file__).with_name("data") / "kasapink-repo")))
IGNORED_PARTS = {".git", "node_modules", "dist", "coverage"}
IGNORED_NAMES = {"credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".db", ".sqlite", ".sqlite3"}
TEXT_SUFFIXES = {
    ".md", ".mdx", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".sql", ".toml", ".css", ".html", ".txt", ".sh", ".ps1",
}
SECRET_LINE = re.compile(
    r"(?:['\"]?(?:password|passwd|secret|token|api[_-]?key|apikey|database[_-]?url|private[_-]?key)['\"]?)\s*[:=]",
    re.I,
)
SECRET_WORD = re.compile(r"\b(?:password|passwd|secret|token|api[_-]?key|apikey|database[_-]?url|private[_-]?key)\b", re.I)
_cache: list[tuple[str, str]] | None = None

REPO_OVERVIEW = (
    "Repo ERP Henka: frontend React 19 + TypeScript + Vite + Tailwind; "
    "backend Express 5 + Node.js 24; database PostgreSQL + Drizzle ORM; "
    "API OpenAPI + Orval + Zod; workspace pnpm; tests Node test runner + Playwright. "
    "Fitur utama: stok bahan, produk/resep, pembelian, produksi/prep, penjualan, stok opname, "
    "kontrol F&B, laporan, dan manajemen akun. Sumber ringkasan: README.md."
)


def _safe_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    lines = []
    for line in text.splitlines():
        if SECRET_LINE.search(line) or ("|" in line and SECRET_WORD.search(line)):
            lines.append("[sensitive configuration line omitted]")
        else:
            lines.append(line)
    return "\n".join(lines)


def _load() -> list[tuple[str, str]]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = []
    if not ROOT.exists():
        return _cache
    # Prune generated/dependency trees before walking them. The previous
    # recursive glob enumerated all of node_modules before ignoring it, which
    # made a large ERP workspace look unavailable to Hermes.
    for base, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_PARTS]
        base_path = Path(base)
        for filename in filenames:
            path = base_path / filename
            if path.name in IGNORED_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if path.name.lower().startswith(".env"):
                continue
            text = _safe_text(path)
            # Prefer human-authored documentation/source over generated bundles.
            if text and (len(text) <= 120_000 or path.name.lower() in {"readme.md", "authentication.md", "development.md", "panduan-pengguna.md"}):
                _cache.append((str(path.relative_to(ROOT)), text))
    return _cache


def search(query: str, limit: int = 6) -> str:
    terms = {term for term in re.findall(r"[a-z0-9_/-]{3,}", query.lower())}
    if not terms:
        return "Repository context (read-only):\n" + REPO_OVERVIEW
    scored: list[tuple[int, str, str]] = []
    for name, text in _load():
        haystack = f"{name}\n{text}".lower()
        score = sum(min(haystack.count(term), 5) for term in terms)
        lower_name = name.lower()
        if any(marker in lower_name for marker in ("readme", "development", "authentication", "panduan", "openapi", "schema")):
            score += 30
        if score == 0:
            continue
        lines = text.splitlines()
        matching = next((i for i, line in enumerate(lines) if any(term in line.lower() for term in terms)), 0)
        start = max(0, matching - 2)
        excerpt = "\n".join(lines[start:start + 14])[:1800]
        scored.append((score, name, excerpt))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return "Repository context: tidak ditemukan potongan yang relevan."
    blocks = [f"[{_personalize_erp_name(name)}]\n{_personalize_erp_name(excerpt)}" for _, name, excerpt in scored[:limit]]
    return "Repository context (read-only):\n" + REPO_OVERVIEW + "\n\n" + "\n\n".join(blocks)


def _personalize_erp_name(text: str) -> str:
    """Keep the connected ERP's project brand out of Hermes chat replies."""
    return re.sub(r"kasapink", "ERP Henka", text, flags=re.I)
