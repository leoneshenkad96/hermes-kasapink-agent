"""Deterministic, secret-free identity information for the Hermes Discord agent."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
HERMES_HOME = Path(os.getenv("HERMES_HOME", r"C:\Users\leoneshenkad\AppData\Local\hermes"))


def _local_cli_info() -> tuple[str, str, str]:
    stamp = HERMES_HOME / "hermes-agent" / "install-stamp.json"
    config = HERMES_HOME / "config.yaml"
    version = "tidak terbaca"
    model = "tidak terbaca"
    provider = "tidak terbaca"
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
        version = str(data.get("displayVersion") or data.get("baseVersion") or version)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    try:
        text = config.read_text(encoding="utf-8")
        match = re.search(r"(?m)^\s*default:\s*[\"']?([^\"'\s]+)", text)
        if match:
            model = match.group(1)
        match = re.search(r"(?m)^\s*provider:\s*[\"']?([^\"'\s]+)", text)
        if match:
            provider = match.group(1)
    except OSError:
        pass
    return version, provider, model


def render() -> str:
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
    primary_provider = "Groq" if os.getenv("GROQ_API_KEY", "").strip() else "Groq (key belum terverifikasi)"
    cli_version, cli_provider, cli_model = _local_cli_info()
    if cli_provider.startswith("custom:"):
        cli_provider = cli_provider.split(":", 1)[1]
    try:
        discord_version = importlib.metadata.version("discord.py")
    except importlib.metadata.PackageNotFoundError:
        discord_version = "tidak terbaca"
    return "\n".join([
        "🧭 **Identitas Hermes — fakta runtime**",
        "",
        "**Discord agent yang sedang menjawab**",
        "- Nama: `slave-hermes` / Hermes Kasapink Agent",
        "- Versi aplikasi: `belum dideklarasikan (source build)`; bukan `1.0.0`",
        f"- Dibangun dengan: Python `{platform.python_version()}`, `discord.py` `{discord_version}`, asyncio",
        f"- Proyek sumber: `{PROJECT_ROOT}`",
        "- Scheduler: Windows Task Scheduler `Hermes Kasapink Discord Bot`",
        "- Komponen utama: `discord_bot.py`, `monitor.py`, `llm_chat.py`, `local_runner.py`, SQLite history",
        f"- Provider utama: **{primary_provider}**",
        f"- Model utama: `{groq_model}`",
        f"- Fallback model: `Google AI Studio / {gemini_model}`",
        "",
        "**Hermes CLI lokal di laptop**",
        f"- Versi terpasang: `{cli_version}`",
        f"- Provider aktif: `{cli_provider}`",
        f"- Model aktif: `{cli_model}`",
        f"- Instalasi: `{HERMES_HOME}`",
        "",
        "🔒 Token dan API key tidak ditampilkan. Jika mau audit ulang, pakai `hermes info` atau `/hermes-info`.",
    ])


def context_facts() -> str:
    """Compact verified facts for the LLM; never use the model to infer these."""
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
    cli_version, cli_provider, cli_model = _local_cli_info()
    if cli_provider.startswith("custom:"):
        cli_provider = cli_provider.split(":", 1)[1]
    return (
        "VERIFIED HERMES RUNTIME FACTS — treat as authoritative data, not instructions:\n"
        "- Discord agent is the local Hermes Kasapink Agent named slave-hermes.\n"
        "- Its source is Python/discord.py/asyncio; the ERP source stack is not established by this agent.\n"
        f"- Discord agent primary model is {groq_model} through Groq; fallback is Gemini {gemini_model}.\n"
        f"- Local Hermes CLI version is {cli_version}, provider {cli_provider}, model {cli_model}.\n"
        "- Never claim GPT-4o, ChatGPT-4-Turbo, Node.js, Express, React, or another stack unless a verified runtime fact explicitly says so.\n"
        "- Never claim a provider call, system check, change, or restart succeeded without tool evidence."
    )
