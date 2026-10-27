"""Discord command and approval layer for the local Hermes monitor."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime, time as clock_time, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

import monitor
import llm_chat

monitor.load_local_env()
import history_store
import anomaly_rules
import correlation
import approval
import erp_auto_policy
import repo_context
import command_templates
import form_parser
import engineering_tasks
import audit_summary
import change_gate
import audit_dashboard
import local_runner
import local_gate
import daily_report
import resource_usage
import stock_market
import product_research
import access_policy
import network_health
import self_heal
import job_executor
import knowledge_base
import memory_store
import identity
import business_advisor
import official_sources
import curated_knowledge
import devops_adapters
import external_adapters
import external_changes
import alert_dedup
import runtime_info
import message_dedup
import intent_router
import permission_guard
from worker_dispatcher import WorkerDispatcher
from file_lock import exclusive

# Python 3.14 + discord.py can format the gateway heartbeat warning badly
# while printing its long traceback. Keep actual gateway errors visible while
# preventing that non-fatal warning from becoming a secondary logging error.
logging.getLogger("discord.gateway").setLevel(logging.ERROR)


def required_int(name: str) -> int:
    value = os.getenv(name, "").strip()
    if not value.isdigit():
        raise RuntimeError(f"{name} is missing or invalid")
    return int(value)


TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
ALERT_CHANNEL_ID = required_int("DISCORD_ALERT_CHANNEL")
COMMAND_CHANNEL_ID = required_int("DISCORD_COMMAND_CHANNEL")
ALERTS_ENABLED = os.getenv("HERMES_ALERTS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
STOCK_CHANNEL_RAW = os.getenv("DISCORD_STOCK_CHANNEL", "").strip()
if STOCK_CHANNEL_RAW and not STOCK_CHANNEL_RAW.isdigit():
    raise RuntimeError("DISCORD_STOCK_CHANNEL must be a Discord channel ID")
STOCK_CHANNEL_ID = int(STOCK_CHANNEL_RAW) if STOCK_CHANNEL_RAW else None
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
ALLOWED_USER_ID = os.getenv("DISCORD_ALLOWED_USER_ID", "").strip()
ADDITIONAL_USER_IDS = access_policy.parse_user_ids(os.getenv("DISCORD_ADDITIONAL_USER_IDS", ""))
ERP_OPERATOR_IDS = access_policy.role_user_ids(ALLOWED_USER_ID, os.getenv("DISCORD_ERP_OPERATOR_IDS", ""))
LOCAL_ACTION_OPERATOR_IDS = access_policy.role_user_ids(ALLOWED_USER_ID, os.getenv("DISCORD_LOCAL_ACTION_OPERATOR_IDS", ""))
INTERVAL_MINUTES = max(5, int(os.getenv("MONITOR_INTERVAL_MINUTES", "30")))
AUDIT_FILE = Path(os.getenv("HERMES_AUDIT_FILE", "./data/command-audit.jsonl"))
IDLE_ALERT_MINUTES = int(os.getenv("HERMES_IDLE_ALERT_MINUTES", "180"))
LAST_IDLE_ALERT = datetime.now(timezone.utc)
conversation_history: dict[tuple[int, int], list[dict[str, str]]] = {}
WORKER_DISPATCHER = WorkerDispatcher()
JAKARTA_TZ = timezone(timedelta(hours=7), name="WIB")
DAILY_REPORT_FILE = Path(os.getenv("HERMES_DAILY_REPORT_STATE", "./data/daily-report.json"))
STOCK_REPORT_FILE = Path(os.getenv("HERMES_STOCK_REPORT_STATE", "./data/stock-report.json"))
CHANNEL_TEST_MARKER = Path(os.getenv("HERMES_CHANNEL_TEST_MARKER", "./data/channel-test-once.json"))

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is missing")
if not ALLOWED_USER_ID.isdigit():
    raise RuntimeError("DISCORD_ALLOWED_USER_ID is missing or invalid; refusing to start with an open command channel")

# Message Content Intent is enabled in the Discord Developer Portal, so
# ordinary text in the command channel is accepted as a command.
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def permitted(interaction: discord.Interaction) -> bool:
    allowed = interaction.channel_id == COMMAND_CHANNEL_ID and str(interaction.user.id) in allowed_user_ids()
    if allowed:
        identity.remember_user(interaction.user)
    return allowed


def reset_idle_timer(now: datetime | None = None) -> None:
    global LAST_IDLE_ALERT
    LAST_IDLE_ALERT = now or datetime.now(timezone.utc)


def idle_alert_due(now: datetime) -> bool:
    global LAST_IDLE_ALERT
    if IDLE_ALERT_MINUTES <= 0 or (now - LAST_IDLE_ALERT) < timedelta(minutes=IDLE_ALERT_MINUTES):
        return False
    LAST_IDLE_ALERT = now
    return True


def permitted_message(message: discord.Message) -> bool:
    allowed = message.channel.id == COMMAND_CHANNEL_ID and str(message.author.id) in allowed_user_ids()
    if allowed:
        identity.remember_user(message.author)
    return allowed


def allowed_user_ids() -> set[str]:
    return {ALLOWED_USER_ID, *ADDITIONAL_USER_IDS}


def alerts_enabled() -> bool:
    """Whether Discord alert-channel delivery is enabled."""
    return ALERTS_ENABLED


def is_mikrotik_owner(user_id: int | str) -> bool:
    return str(user_id) == ALLOWED_USER_ID


def can_write_erp(user_id: int | str) -> bool:
    return str(user_id) in ERP_OPERATOR_IDS


def can_request_local_action(user_id: int | str) -> bool:
    return str(user_id) in LOCAL_ACTION_OPERATOR_IDS


def can_access_mikrotik(user_id: int | str, kind: str, target: str = "") -> bool:
    return is_mikrotik_owner(user_id) or not access_policy.mikrotik_only_command(kind, target)


def permission_level_for(kind: str, *, approved: bool = False) -> permission_guard.PermissionLevel:
    """Expose the command capability contract for adapters and tests."""
    return permission_guard.level_for(kind, approved=approved)


async def reject(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Perintah hanya boleh digunakan di channel Hermes dan oleh user yang diizinkan.",
        ephemeral=True,
    )


def status_text(snapshot: dict) -> str:
    icons = {"kasapink": "🧾", "core_gateway": "🌐", "lan_controller": "🔗"}
    labels = {"kasapink": "ERP Henka", "core_gateway": "Core Gateway", "lan_controller": "LAN Controller"}
    lines = ["📊 **Hermes Bossun — status read-only**"]
    for name, data in snapshot.get("systems", {}).items():
        status = data.get("status", "UNKNOWN")
        icon = "✅" if status == "OK" else ("⚠️" if status not in {"ERROR", "NOT_CONFIGURED"} else "🚨")
        lines.append(f"- {icons.get(name, '🔹')} **{labels.get(name, name)}**: {icon} **{status}**")
    return "\n".join(lines)


def hermes_build_status(snapshot: dict | None, task_scheduler: str, gateway_ready: bool, latency_ms: float | None) -> str:
    """Summarize Hermes itself; keep the ERP/router data status command separate."""
    gateway = f"✅ online · ping {latency_ms:.0f} ms" if gateway_ready and latency_ms is not None else (
        "✅ online" if gateway_ready else "⚠️ belum tersambung"
    )
    lines = [
        "🧠 **Hermes Bossun — status build**",
        f"- Bot Discord: {gateway}",
        f"- Windows Task Scheduler: **{task_scheduler.strip() or 'tidak diketahui'}**",
        f"- Chat AI: **{'API key tersedia; validitas cek lewat /provider-status' if (os.getenv('GROQ_API_KEY', '').strip() or os.getenv('GOOGLE_API_KEY', '').strip() or os.getenv('GEMINI_API_KEY', '').strip()) else 'belum dikonfigurasi'}**",
        f"- Channel Discord: **{'terkonfigurasi' if ALERT_CHANNEL_ID and COMMAND_CHANNEL_ID else 'konfigurasi belum lengkap'}**",
        f"- Hak akses: **{len(allowed_user_ids())} user terdaftar** · operator ERP **{len(ERP_OPERATOR_IDS)}** · operator aksi laptop **{len(LOCAL_ACTION_OPERATOR_IDS)}** · MikroTik **owner-only**",
        "\n**Modul yang tersedia**",
        "- ERP & MikroTik: pemantauan read-only, baseline, diff, dan deteksi anomali.",
        "- ERP: template input dan perubahan terbatas melalui proposal + approval.",
        "- Laptop: diagnostik terbatas; restart Hermes/laptop perlu approval.",
        "- Saham: data historis, perbandingan tanggal, dan tren IHSG; rekap terjadwal.",
        "- Engineering: task lokal untuk handoff; approval merge/deploy hanya mencatat persetujuan, bukan mengeksekusi deploy.",
        "- DevOps: registry 15 domain, probe tool lokal, workflow incident/programmer/DevOps, dan koneksi eksternal tetap read-only sampai disetujui.",
    ]
    captured = snapshot.get("captured_at") if isinstance(snapshot, dict) else None
    systems = snapshot.get("systems", {}) if isinstance(snapshot, dict) else {}
    lines.append("\n**Snapshot sistem terakhir (bukan live refresh)**")
    if captured and isinstance(systems, dict):
        try:
            captured_local = datetime.fromisoformat(str(captured).replace("Z", "+00:00")).astimezone(JAKARTA_TZ)
            captured_text = captured_local.strftime("%d %b %Y %H:%M WIB")
        except ValueError:
            captured_text = str(captured)
        labels = {"kasapink": "ERP Henka", "core_gateway": "Core Gateway", "lan_controller": "LAN Controller"}
        lines.append(f"- Waktu: `{captured_text}`")
        for key, label in labels.items():
            data = systems.get(key, {})
            state = data.get("status", "UNKNOWN") if isinstance(data, dict) else "UNKNOWN"
            mark = "✅" if state == "OK" else "🚨" if state == "ERROR" else "⚠️"
            lines.append(f"- {label}: {mark} **{state}**")
    else:
        lines.append("- Belum ada snapshot tersimpan.")
    if not local_runner.enabled():
        lines.append("\n🔒 Aksi lokal sedang dinonaktifkan oleh kill switch.")
    return "\n".join(lines)


def format_local_diagnostic(target: str, result: dict) -> str:
    if target == "chatgpt":
        state = result.get("status", "UNKNOWN")
        mark = "✅" if state == "RUNNING" else "⚠️" if state == "UNRESPONSIVE" else "❌"
        task = result.get("startup_task", {}) if isinstance(result.get("startup_task"), dict) else {}
        return ("🧠 **ChatGPT / Codex Desktop**\n"
                f"{mark} Status aplikasi: **{state}**\n"
                f"🔢 Proses ChatGPT: **{result.get('process_count', 0)}** · responsif **{result.get('responding_count', 0)}**\n"
                f"🚀 Startup task: **{'tersedia' if task.get('present') else 'tidak ditemukan'}** · state `{task.get('state', 'UNKNOWN')}`\n"
                "Keterangan: pemeriksaan ini hanya membaca proses dan task startup.")
    if result.get("status") != "OK":
        return f"💻 **Diagnostik laptop — {target}**\n⚠️ Status: **{result.get('status', 'UNKNOWN')}**\n{result.get('reason', '')}"
    if target == "system":
        usage = result.get("usage", {})
        if isinstance(usage, dict):
            cpu = usage.get("cpu_usage_percent", "?")
            total = usage.get("memory_total_gb", "?")
            free = usage.get("memory_free_gb", "?")
            try:
                used = round(float(total) - float(free), 2)
                memory_icon = "⚠️" if float(free) < 2 else "🧠"
                memory_line = f"{memory_icon} RAM: **{used} GB terpakai** dari {total} GB (sisa {free} GB)"
            except (TypeError, ValueError):
                memory_line = f"🧠 RAM: **{free} GB tersisa** dari {total} GB"
            return ("💻 **Kesehatan Laptop**\n"
                    "✅ Status: **Normal**\n\n"
                    f"🔥 CPU usage: **{cpu}%**\n"
                    f"{memory_line}\n"
                    f"⚙️ CPU thread/core terdeteksi: **{result.get('cpu_count', '?')}**\n"
                    f"🪟 OS: `{result.get('os', '?')}`")
    if target == "disk":
        return ("💾 **Penyimpanan Laptop**\n✅ Status: **Normal**\n"
                f"🟢 Ruang kosong: **{result.get('free_gb', '?')} GB** dari {result.get('total_gb', '?')} GB\n"
                f"📁 Lokasi kerja: `{result.get('path', '?')}`")
    if target == "wireguard":
        return "🛡️ **WireGuard**\n✅ Status: **Terbaca**\n```json\n" + str(result.get("wireguard", "No data."))[:1200] + "\n```"
    if target == "network":
        return "🌐 **Jaringan Laptop**\n✅ Adapter berhasil dibaca.\n```json\n" + str(result.get("adapters", "No data."))[:1200] + "\n```"
    if target == "processes":
        return "🧩 **Proses/Aplikasi Aktif**\n✅ Daftar proses berhasil dibaca.\n```json\n" + str(result.get("processes", "No data."))[:1200] + "\n```"
    return f"🤖 **Hermes di Laptop**\n✅ Task Scheduler: `{result.get('task_scheduler', '?')}`\n🔒 Kill switch: `{result.get('kill_switch', False)}`"


def format_self_heal(result: dict) -> str:
    """Render diagnostics for people, not as nested implementation JSON."""
    if {"runtime", "bot", "hermes", "network", "disk", "chatgpt"}.issubset(result):
        runtime = result.get("runtime", {})
        bot_state = result.get("bot", {})
        hermes = result.get("hermes", {})
        network = result.get("network", {})
        disk = result.get("disk", {})
        chatgpt = result.get("chatgpt", {})
        ready = bool(bot_state.get("ready"))
        latency = bot_state.get("latency_ms")
        disk_free = disk.get("free_gb")
        disk_total = disk.get("total_gb")
        disk_pct = round(float(disk_free) / float(disk_total) * 100, 1) if disk_free is not None and disk_total else None
        lines = ["🩺 **Hermes — hasil self-diagnose**", ""]
        lines.append(f"🤖 Bot Discord: {'✅ Online' if ready else '❌ Offline'}" + (f" · latency {latency:.0f} ms" if isinstance(latency, (int, float)) else ""))
        lines.append(f"⚙️ Task Hermes: {'✅ Berjalan' if hermes.get('task_scheduler') == 'Running' else '⚠️ ' + str(hermes.get('task_scheduler', 'Tidak diketahui'))}")
        lines.append(f"🌐 Jaringan: {'✅ Tersedia' if network.get('status') == 'OK' else '❌ Bermasalah'}")
        chatgpt_state = chatgpt.get("status", "UNKNOWN")
        lines.append(f"🧠 ChatGPT/Codex: {'✅ Aktif' if chatgpt_state == 'RUNNING' else '⚠️ ' + str(chatgpt_state)}")
        if disk_pct is not None:
            lines.append(f"💾 Disk: ✅ {disk_free} GB kosong dari {disk_total} GB ({disk_pct}%)")
        lines.append(f"🖥️ Sistem: {runtime.get('os', 'Tidak diketahui')} · Python {runtime.get('python', '?')}")
        lines.append(f"🔒 Kill switch: {'Aktif' if hermes.get('kill_switch') else 'Tidak aktif'}")
        lines.append("\n✅ Tidak ditemukan masalah kritis pada pemeriksaan ini." if ready and hermes.get("task_scheduler") == "Running" and network.get("status") == "OK" and chatgpt_state == "RUNNING" else "\n⚠️ Ada pemeriksaan yang perlu ditindaklanjuti.")
        return "\n".join(lines)[:1800]
    if result.get("status") in {"STARTED", "START_REQUESTED", "ALREADY_RUNNING"} and result.get("task") == "ChatGPT At User Logon":
        labels = {
            "STARTED": "ChatGPT berhasil dinyalakan dan terverifikasi.",
            "START_REQUESTED": "Permintaan menyalakan ChatGPT sudah dikirim, tetapi proses belum terverifikasi dalam batas tunggu.",
            "ALREADY_RUNNING": "ChatGPT sudah aktif; tidak ada proses tambahan yang dibuat.",
        }
        return f"🧠 **ChatGPT / Codex Desktop**\n✅ {labels[result['status']]}\n🚀 Task: `ChatGPT At User Logon`\n🛡️ Tidak ada credential atau data aplikasi yang dibaca."
    if result.get("status") == "RESTART_REQUESTED":
        return "🛠️ **Hermes self-heal**\n✅ Restart Hermes sudah diminta.\n🔁 Task: `Hermes Kasapink Discord Bot`\n🛡️ Mode: aman, mencegah instance ganda."
    return "🩺 **Hermes self-heal**\n" + json.dumps(result, ensure_ascii=False)[:1600]


def format_local_action_result(action: str, result: dict) -> str:
    if action in {"start_chatgpt", "restart_chatgpt"}:
        labels = {
            "STARTED": "ChatGPT berhasil dinyalakan dan terverifikasi.",
            "START_REQUESTED": "Permintaan sudah dikirim, tetapi ChatGPT belum terverifikasi dalam batas tunggu.",
            "ALREADY_RUNNING": "ChatGPT sudah aktif; tidak ada instance tambahan yang dibuat.",
        }
        status = result.get("status", "UNKNOWN")
        return (f"🧠 **{'Restart' if action == 'restart_chatgpt' else 'Start'} ChatGPT/Codex**\n"
                f"{'✅' if status in {'STARTED', 'ALREADY_RUNNING'} else '⚠️'} {labels.get(status, 'Aksi selesai dengan status ' + str(status) + '.') }\n"
                f"🚀 Task: `{result.get('task', 'ChatGPT At User Logon')}`\n"
                + ("📣 Konfirmasi online akan dikirim ke channel alert setelah proses terverifikasi.\n" if action == "restart_chatgpt" else "")
                + "🔒 Tidak ada credential atau isi percakapan yang dibaca.")
    if action == "restart_hermes" and result.get("status") in {"START_REQUESTED", "ALREADY_RUNNING"}:
        return ("🛠️ **Restart Hermes**\n"
                "✅ Permintaan restart diproses tanpa membuat instance ganda.\n"
                "📣 Hermes akan mengirim pemberitahuan ke channel alert setelah kembali online.")
    return local_runner.render(result)


def format_terminal_preset(result: dict) -> str:
    preset = result.get("preset", "terminal")
    raw = str(result.get("output", "")).strip()
    if preset == "network":
        ok = raw.lower() == "true"
        return f"🧪 **PowerShell — koneksi Discord**\n{'✅ Port Discord 443 dapat dijangkau.' if ok else '❌ Port Discord 443 tidak dapat dijangkau.'}\n\nKeterangan: pemeriksaan ini hanya menguji koneksi HTTPS, bukan token."
    if preset == "dns_discord":
        try:
            records = json.loads(raw)
            count = len(records) if isinstance(records, list) else 0
        except json.JSONDecodeError:
            count = 0
        return f"🧪 **PowerShell — DNS Discord**\n{'✅ discord.com berhasil diterjemahkan ke DNS.' if count else '❌ DNS discord.com gagal diterjemahkan.'}\n\nJumlah alamat ditemukan: **{count}**\nKeterangan: alamat IP disembunyikan agar laporan tetap ringkas."
    if preset == "hermes_task":
        try:
            task = json.loads(raw)
            state = task.get("State") if isinstance(task, dict) else None
        except json.JSONDecodeError:
            state = None
        return f"🧪 **PowerShell — Scheduled Task Hermes**\n{'✅ Task sedang berjalan.' if state == 4 else '⚠️ Task tidak terdeteksi berjalan.'}\n\nKeterangan: task ini menjaga bot Discord tetap aktif."
    if preset == "processes":
        try:
            processes = json.loads(raw)
            processes = processes if isinstance(processes, list) else [processes]
            names = sorted({str(p.get("ProcessName", "tidak diketahui")) for p in processes if isinstance(p, dict)})
        except json.JSONDecodeError:
            names = []
        return f"🧪 **PowerShell — proses penting**\n✅ Ditemukan **{len(names)} jenis proses** terkait runtime.\n\nProses: `{', '.join(names) if names else 'tidak terbaca'}`\nKeterangan: PID dan waktu start disembunyikan."
    if preset == "system":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return f"🧪 **PowerShell — ringkasan sistem**\n🖥️ Komputer: `{data.get('computer', 'tidak diketahui')}`\n🧠 RAM kosong: **{data.get('memory_free_gb', '?')} GB**\nKeterangan: hanya informasi kesehatan dasar; tidak ada perubahan sistem."
    return f"🧪 **PowerShell — {preset}**\n✅ Pemeriksaan selesai.\nKeterangan: output teknis disembunyikan dari tampilan utama."


def format_job(record: dict) -> str:
    status_labels = {
        "PLANNED": "📝 Rencana siap dijalankan",
        "AWAITING_APPROVAL": "🔐 Menunggu approval",
        "APPROVED": "✅ Disetujui, siap dijalankan",
        "RUNNING": "🔄 Sedang berjalan",
        "SUCCEEDED": "✅ Selesai",
        "FAILED": "❌ Gagal",
    }
    lines = [f"📦 **Job `{record.get('id', '?')}`**", f"Jenis: `{record.get('kind', 'general')}`", f"Status: **{status_labels.get(record.get('status'), record.get('status', '?'))}**"]
    if record.get("approval_required"):
        lines.append("🔒 Job ini memerlukan approval sebelum aksi dijalankan.")
    lines.append(f"⚠️ Risiko: {record.get('risk', 'Belum ditentukan')}")
    steps = record.get("steps", [])
    if steps:
        lines.append("\n**Rencana langkah:**")
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, 1))
    if record.get("result", {}).get("summary"):
        lines.append(f"\n📄 Hasil: {record['result']['summary']}")
    if record.get("error"):
        lines.append(f"\n⛔ Error: `{record['error']}`")
    return "\n".join(lines)[:1900]


def audit(event: str, **details: object) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": monitor.now(), "event": event, **details}
    with AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def split_discord_message(text: str, limit: int = 1800) -> list[str]:
    """Split long replies below Discord's 2,000 UTF-16-unit content limit."""
    text = text.strip()
    if not text:
        return ["(Hermes tidak menghasilkan teks balasan.)"]
    if not 1 <= limit <= 2000:
        raise ValueError("limit pesan Discord harus antara 1 dan 2000")

    chunks: list[str] = []
    while len(text.encode("utf-16-le")) // 2 > limit:
        used_units = 0
        max_index = 0
        for index, character in enumerate(text):
            units = 2 if ord(character) > 0xFFFF else 1
            if used_units + units > limit:
                break
            used_units += units
            max_index = index + 1
        # Search only within the budgeted prefix; max_index itself may point
        # at the first code point that did not fit (notably a non-BMP emoji).
        boundary = text.rfind("\n", 0, max_index)
        if boundary < max_index // 2:
            boundary = text.rfind(" ", 0, max_index)
        if boundary <= 0:
            boundary = max_index
        elif text[boundary] in "\n ":
            boundary += 1
        chunks.append(text[:boundary])
        text = text[boundary:]
    if text:
        chunks.append(text)
    return chunks


async def safe_natural_reply(channel_id: int, text: str, requester_id: int, requester_name: str) -> str:
    try:
        return await natural_reply(channel_id, text, requester_id, requester_name)
    except Exception as exc:
        audit("chat_handler_error", error=type(exc).__name__, requester_id=requester_id)
        return "⚠️ Gue gagal memproses pesan ini. Error-nya sudah dicatat tanpa menyimpan isi pesan; coba lagi sebentar."


def evaluate_snapshot() -> tuple[str, dict, list[dict], list[str]]:
    state_path = Path(os.getenv("MONITOR_STATE_FILE", "./data/baseline.json"))
    previous = monitor.load_json(state_path)
    current = monitor.collect(False)
    systems = current.get("systems", {})
    errors = [name for name, data in systems.items() if data.get("status") == "ERROR"]
    previous_invalid = bool(previous and any(
        data.get("status") == "ERROR"
        for data in previous.get("systems", {}).values()
        if isinstance(data, dict)
    ))
    current_hash = monitor.stable_hash(systems)
    previous_hash = monitor.stable_hash(previous.get("systems", {})) if previous else None
    if errors:
        event = "ERROR"
    elif previous is None or previous_invalid:
        event = "RECOVERY" if previous_invalid else "BASELINE_CREATED"
    elif current_hash == previous_hash:
        event = "NO_CHANGE"
    else:
        event = "CHANGE_DETECTED"
    anomalies: list[dict] = []
    if event in {"BASELINE_CREATED", "CHANGE_DETECTED", "NO_CHANGE"}:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        snapshot_id, history_previous = history_store.append_snapshot(current["captured_at"], event, current_hash, current)
        anomalies = anomaly_rules.detect(history_previous, current)
        anomalies += correlation.correlate(anomalies)
        history_store.append_anomalies(snapshot_id, anomalies)
    changes = monitor.describe_changes(previous, current) if event == "CHANGE_DETECTED" else []
    return event, current, anomalies, changes


def alert_text(event: str, snapshot: dict, changes: list[str] | None = None) -> str:
    heading = {
        "ERROR": "🚨 Hermes Monitor — error",
        "RECOVERY": "✅ Hermes Monitor — recovery",
        "CHANGE_DETECTED": "⚠️ Hermes Monitor — perubahan terdeteksi",
        "BASELINE_CREATED": "✅ Hermes Monitor — baseline dibuat",
        "NO_CHANGE": "🔐 Hermes Monitor — temuan penting",
    }[event]
    message = heading + "\n" + status_text(snapshot)
    if event in {"CHANGE_DETECTED", "NO_CHANGE"}:
        details = changes or ["Snapshot berubah, tetapi tidak ada field detail yang dapat diidentifikasi."]
        message += "\n\n**Rincian perubahan**\n" + "\n".join(f"- {item}" for item in details)
    return message


def normalize_command(text: str) -> str:
    value = " ".join(text.lower().strip().split())
    for prefix in ("hermes", "tolong", "please"):
        if value.startswith(prefix + " "):
            value = value[len(prefix):].strip()
    return value


def classify_command(text: str) -> tuple[str, str]:
    """Deterministic safe router; usable even without an LLM API key."""
    raw_value = " ".join(text.lower().strip().split())
    value = normalize_command(text)
    question_like = value.endswith("?") or any(term in value for term in (
        "apakah", "bisa gak", "bisa nggak", "udah bisa", "sudah bisa", "bisa ya",
        "gimana", "boleh gak", "boleh nggak", "kenapa", "kok",
    ))
    ram_query = any(term in value for term in ("ram", "memory", "memori"))
    educational_ram = any(term in value for term in ("apa itu", "fungsi ram", "kebutuhan ram", "ram yang dibutuhkan", "berapa ram yang dibutuhkan", "biasanya dibutuhkan"))
    if ram_query and not educational_ram and any(term in value for term in ("usage", "pemakaian", "penggunaan", "terpakai", "sisa", "berapa", "cek", "kalo ram", "kalau ram")):
        source_count = sum((
            any(term in value for term in ("laptop", "pc", "gue", "saya")),
            any(term in value for term in ("server", "erp")),
            any(term in value for term in ("mikrotik", "router")),
        ))
        if any(term in value for term in ("semua", "semuanya", "ketiganya")) or source_count > 1:
            return "resource_usage", "all"
        if any(term in value for term in ("mikrotik", "router")):
            return "resource_usage", "mikrotik"
        if any(term in value for term in ("erp", "server")):
            return "resource_usage", "erp"
        if any(term in value for term in ("laptop", "pc", "gue", "saya")):
            return "resource_usage", "laptop"
        return "resource_usage", "all"
    comparison_dates = stock_market.extract_dates(text)
    if len(comparison_dates) >= 2 and any(word in value for word in ("saham", "naik", "turun", "kenaikan", "perubahan", "banding")):
        ticker = re.search(r"\b(?:saham|untuk|ticker|kode)\s+([A-Z0-9]{4,5})\b", text, re.I) or re.search(r"\b([A-Z0-9]{4,5})\b", text)
        if ticker:
            return "stock_compare", f"{ticker.group(1).upper()}|{comparison_dates[0]}|{comparison_dates[1]}"
    if any(term in value for term in ("rata-rata", "rata rata", "average")) and any(term in value for term in ("saham", "setahun", "1 tahun", "12 bulan")):
        ticker = re.search(r"\b(?:saham|untuk|ticker|kode)\s+([A-Z0-9]{4,5})\b", text, re.I) or re.search(r"\b([A-Z0-9]{4,5})\b", text)
        if ticker:
            return "stock_average", ticker.group(1).upper()
    stock_match = re.search(r"\b(?:cek\s+)?(?:saham|analisis\s+saham|harga\s+saham)\s+([A-Z0-9][A-Z0-9._-]{0,19})\b", text, re.I)
    if stock_match:
        return "stock_lookup", stock_match.group(1).upper()
    product_intent = intent_router.route_product_search(text)
    if product_intent is not None:
        return "product_search", text
    if business_advisor.parse_price_question(text):
        return "business_price", text
    if not question_like and any(term in value for term in (
        "restart chatgpt", "mulai ulang chatgpt", "chatgpt error", "chatgpt bermasalah",
        "chatgpt sedang error", "codex error", "codex bermasalah", "codex macet", "codex hang",
        "codex tidak aktif", "codex gak aktif", "codex nggak aktif", "codex tidak jalan",
        "restart codex", "perbaiki chatgpt",
    )):
        return "local_action_proposal", "restart_chatgpt"
    if not question_like and any(term in value for term in (
        "aktifkan chatgpt", "nyalakan chatgpt", "hidupkan chatgpt", "buka chatgpt",
        "aktifkan codex", "nyalakan codex", "buka codex", "start chatgpt",
    )):
        return "local_action_proposal", "start_chatgpt"
    if any(word in value for word in ("cek laptop", "kesehatan laptop", "cek komputer", "cek chatgpt", "status chatgpt", "cek codex", "status codex", "cek wireguard", "cek proses", "cek aplikasi", "cek jaringan laptop", "cek disk laptop", "cek hermes laptop", "storage laptop", "penyimpanan laptop", "ruang disk laptop", "sisa storage", "cek cpu", "cpu usage", "penggunaan cpu", "pemakaian cpu", "cek ram", "penggunaan ram", "pemakaian ram")):
        target = "system"
        if "wireguard" in value: target = "wireguard"
        elif "proses" in value or "aplikasi" in value: target = "processes"
        elif "jaringan" in value: target = "network"
        elif "disk" in value or "storage" in value or "penyimpanan" in value or "ruang disk" in value: target = "disk"
        elif "hermes" in value: target = "hermes"
        elif "chatgpt" in value or "codex" in value: target = "chatgpt"
        return "laptop_status", target
    if any(term in value for term in ("token provider", "provider token", "api key provider", "status provider", "cek provider", "cek groq", "cek gemini", "cek google ai")):
        return "provider_status", ""
    identity_terms = ("dibuild", "dibangun", "dibuat", "model", "versi", "stack", "framework", "library", "backend", "frontend")
    if value in {"--version", "-v", "version", "model", "info", "about"} or ("hermes" in raw_value and any(term in value for term in identity_terms)) or any(term in value for term in (
        "versi hermes", "hermes versi", "info hermes", "tentang hermes", "hermes dibangun",
        "hermes dibuat", "model hermes", "model yang dipakai", "model yang digunakan",
        "stack hermes", "build hermes", "hermes pakai apa", "hermes dibuild",
        "dibangun pake", "dibangun pakai", "dibuild pake", "dibuild pakai", "dibuat pake", "dibuat pakai",
    )):
        return "runtime_info", ""
    task_create = any(term in value for term in ("buat", "buatkan", "bikinin", "create"))
    task_word = any(term in value for term in ("task", "tugas"))
    security_audit = any(term in value for term in ("security audit", "audit security", "audit keamanan", "keamanan erp"))
    if not question_like and task_create and task_word and security_audit:
        return "engineering_security_task", value
    if not question_like and any(term in value for term in ("restart laptop", "reboot laptop", "restart komputer", "reboot komputer", "mulai ulang laptop", "nyalakan ulang laptop")):
        return "local_action_proposal", "restart_laptop"
    if not question_like and any(term in value for term in ("restart hermes", "jalankan ulang hermes", "nyalakan ulang hermes")):
        return "local_action_proposal", "restart_hermes"
    if command_templates.is_latest_stock_query(value):
        return "stock_latest", ""
    if command_templates.match(value):
        return "template", value
    if value in {"help", "bantuan", "apa yang bisa kamu lakukan"} or (
        "help" in value and any(term in value for term in ("menu", "daftar", "apa aja", "apa saja"))
    ) or any(term in value for term in (
        "sekarang lu bisa apa aja", "lu bisa apa aja", "kamu bisa apa saja",
        "bisa ngapain", "kemampuan kamu", "fitur kamu", "fitur hermes",
        "perintah apa saja", "bisa bantu apa",
    )):
        return "help", ""
    if any(word in value for word in ("diff", "perubahan", "baseline")):
        return "diff", ""
    if not question_like and any(word in value for word in ("ubah", "hapus", "matikan", "restart", "disable", "enable", "update", "buat")):
        return "mutation_blocked", value
    # Only explicit monitoring language is routed to a status tool. A normal
    # question that merely mentions ERP/Core/LAN must go to the chat model.
    explicit_check = any(word in value for word in ("status", "cek", "check", "health", "kondisi", "pantau", "monitor"))
    asks_isp = any(word in value for word in ("isp", "wan", "provider"))
    if explicit_check and asks_isp:
        return "isp_status", "core_gateway"
    if explicit_check and ("core" in value or "gateway" in value):
        return "status", "core_gateway"
    if explicit_check and ("lan controller" in value or "lan" in value):
        return "status", "lan_controller"
    if explicit_check and any(word in value for word in ("audit", "menu", "erp")):
        return "status", "kasapink"
    if explicit_check:
        return "status", "all"
    return "unknown", value


def help_text() -> str:
    return (
        "**Perintah Hermes**\n"
        "`/status` — cek semua sistem\n"
        "`/status-hermes` — cek kondisi build/bot Hermes dan snapshot terakhir\n"
        "`/hermes-info` — fakta versi, stack, provider, model, dan instalasi Hermes\n"
        "`/devops-status` — cek 15 domain DevOps dan adapter lokal tanpa menampilkan credential\n"
        "`/external-status` — cek GitHub, Vercel, dan Neon read-only\n"
        "`/external-propose` — siapkan write allowlist dengan payload dan approval\n"
        "`/external-approve` / `/external-deny` — eksekusi atau batalkan write external\n"
        "`/business question` — analisis harga, HPP, dan margin dari data ERP read-only\n"
        "`/business-report` — ringkasan finance/F&B berdasarkan periode ERP\n"
        "`/fnb question` — diskusi operasi makanan, SOP, costing, stok, dan menu\n"
        "`/parfum question` — diskusi notes, keluarga aroma, rekomendasi, dan keamanan parfum\n"
        "`/sources topic` — lihat sumber resmi, version, tanggal verifikasi, citation, dan jadwal review\n"
        "`/knowledge-sources topic` — lihat peta knowledge yang lu berikan dan tingkat kepercayaan sumber\n"
        "`/devops-plan stage` — buat rencana Tool Adapter, Incident, Programmer, atau DevOps workflow\n"
        "`/diagnose` — self-diagnose Hermes, Discord, jaringan, dan laptop\n"
        "`/self-heal mode:diagnose|repair` — diagnosis atau restart Hermes allowlisted\n"
        "`/terminal preset` — preset PowerShell diagnostik read-only\n"
        "`/check target` — cek `erp`, `core`, `lan`, atau `all`\n"
        "`/diff` — bandingkan dengan baseline\n"
        "`/command teks` — perintah bahasa natural read-only\n"
        "Input ERP rutin yang lengkap dan jelas dapat dicatat otomatis jika nilainya ≤ Rp100.000; aksi lain tetap memakai approval.\n"
        "Hak tulis ERP dan aksi sistem laptop hanya untuk operator yang didaftarkan; default-nya owner.\n"
        "`/propose method endpoint json` — siapkan perubahan ERP untuk approval\n"
        "`/approve ID` — jalankan proposal yang sudah diperiksa\n"
        "`/deny ID` — batalkan proposal\n\n"
        "`/approval-status ID` — cek status proposal milik lu; jika `UNKNOWN`, cek ERP read-only dan jangan kirim ulang.\n\n"
        "`/saham kode` — ringkasan historis IDX. Tanya juga `rata-rata saham ASII setahun` atau `ASII naik nggak dari 2025-01-01 sampai 2025-06-30`. Setiap hari pukul 06.00 WIB ada rekap ERP/MikroTik di #hermes-alert.\n\n"
        "Untuk mencari produk, harga, perbandingan, atau rekomendasi marketplace, tanya natural seperti `Carikan tablet untuk gambar dan nonton, bandingkan harga dan speknya`. Harga dari indeks pencarian bersifat indikatif; tautan sumber disertakan.\n\n"
        "Untuk alur ERP, tulis misalnya `tambah stok makanan`, `tambah produk parfum`, `atur resep`, "
        "`catat belanja`, `catat penjualan`, atau `stok opname`; Hermes akan mengirim template isian. "
        "Produk/harga, resep, opname, tanggal lama, data ambigu, dan nilai di atas batas tetap memerlukan approval.\n\n"
        "`/eng-task` — buat task untuk Codex/ChatGPT\n"
        "`/eng-list` — daftar task engineering terbuka\n"
        "`/audit` — ringkasan history/anomaly\n"
        "`/change-propose` dan `/change-approve` — gate merge/deploy\n\n"
        "`/job` — buat rencana pekerjaan dengan risiko dan approval\n"
        "`/job-approve`, `/job-run`, `/job-status`, `/job-list` — kelola job\n"
        "`/job-schedule` dan `/job-schedules` — job harian otomatis dalam WIB\n\n"
        "`/remember` — simpan fakta/preferensi secara eksplisit ke memory lokal\n"
        "`/memory` — lihat memory dan ringkasan percakapan yang bisa diakses\n"
        "`/forget` — hapus memory berdasarkan ID\n\n"
        "`/whoami` — lihat user Discord yang sedang dikenali Hermes\n"
        "`/users` — daftar user yang sudah command ke Hermes (owner)\n\n"
        "`/laptop-check target` — diagnostik laptop read-only, termasuk status ChatGPT/Codex\n"
        "`/laptop-propose action:start_chatgpt|restart_chatgpt|restart_hermes|restart_laptop` — siapkan aksi dengan approval\n"
        "`/approved ID` — jalankan approval aksi laptop\n"
        "`/laptop-approve ID` — alias lama untuk approval aksi laptop\n\n"
        "Approval berlaku 10 menit, satu kali pakai, dan hanya bisa dikonfirmasi pembuatnya. "
        "Executor awal hanya mendukung create/update ERP; penghapusan dan konfigurasi MikroTik masih dikunci."
    )


async def run_readonly(kind: str, target: str = "all") -> str:
    if kind == "diff":
        state_path = Path(os.getenv("MONITOR_STATE_FILE", "./data/baseline.json"))
        previous = monitor.load_json(state_path)
        current = await asyncio.to_thread(monitor.collect, False)
        if not previous:
            return "Baseline belum tersedia."
        if monitor.stable_hash(current.get("systems", {})) == monitor.stable_hash(previous.get("systems", {})):
            return "Tidak ada perubahan sejak baseline terakhir."
        detected = anomaly_rules.detect(previous, current)
        detected += correlation.correlate(detected)
        return "Ada perubahan sejak baseline terakhir.\n\n" + anomaly_rules.render(detected)

    snapshot = await asyncio.to_thread(monitor.collect, False)
    if kind == "stock_latest":
        # The current ERP state is the newest read-only stock view available;
        # recording a physical count is a separate mutating workflow.
        data = snapshot.get("systems", {}).get("kasapink", {})
        state = data.get("state", {}) if isinstance(data, dict) else {}
        ingredients = state.get("ingredients", []) if isinstance(state, dict) else []
        preparations = state.get("preparations", []) if isinstance(state, dict) else []
        low = [item for item in ingredients + preparations if isinstance(item, dict) and item.get("stock", 0) <= item.get("minStock", 0)]
        lines = ["**Stok terbaru ERP Henka**", f"Diambil: `{snapshot.get('captured_at', 'waktu tidak tersedia')}`", f"- Bahan: **{len(ingredients)}**", f"- Preparation: **{len(preparations)}**", f"- Perlu dicek/menipis: **{len(low)}**"]
        if low:
            lines.append("\n**Item yang perlu diperhatikan:**")
            lines.extend(f"- {item.get('name', '?')}: {item.get('stock', 0)} {item.get('unit', '')} (minimum {item.get('minStock', 0)})" for item in low[:20])
        return "\n".join(lines) if data.get("status") == "OK" else "Stok ERP belum bisa dibaca: " + str(data.get("status", "UNKNOWN"))
    if target == "all":
        return status_text(snapshot)
    system_name = {"erp": "kasapink", "core": "core_gateway", "lan": "lan_controller"}.get(target, target)
    data = snapshot.get("systems", {}).get(system_name)
    if not data:
        return f"Target `{target}` tidak ditemukan."
    return f"**{target}**: **{data.get('status', 'UNKNOWN')}**"


async def _dispatch_text(text: str, requester_id: int | None = None) -> str:
    kind, value = classify_command(text)
    if kind == "business_price":
        try:
            result = await asyncio.to_thread(business_advisor.analyze_price, value)
            audit("business_price_analysis", status=result.get("status"), product=result.get("product_name") or result.get("query"), requester_id=requester_id)
            return business_advisor.render_price(result)
        except Exception as exc:
            audit("business_price_analysis_failed", error=type(exc).__name__, requester_id=requester_id)
            return "⚠️ Analisis bisnis belum bisa mengambil data ERP saat ini. Tidak ada transaksi atau harga yang diubah; coba lagi setelah ERP siap."
    if kind == "resource_usage":
        owner = requester_id is not None and is_mikrotik_owner(requester_id)
        snapshot = None
        laptop = None
        if owner and value in {"all", "mikrotik"}:
            snapshot = await asyncio.to_thread(monitor.collect, False)
        if owner and value in {"all", "laptop"}:
            laptop = await asyncio.to_thread(local_runner.diagnose, "system")
        audit("resource_usage_read", scope=value, requester_id=requester_id)
        return resource_usage.render(
            laptop=laptop, snapshot=snapshot, can_see_laptop=owner,
            can_see_mikrotik=owner, scope=value,
        )
    if kind == "product_search":
        if not os.getenv("BRAVE_SEARCH_API_KEY", "").strip():
            return ("🛍️ Pencarian marketplace belum diaktifkan. Tambahkan `BRAVE_SEARCH_API_KEY` "
                    "ke `.env` lokal Hermes, lalu restart bot. Jangan kirim API key ke Discord.")
        try:
            results = await asyncio.to_thread(product_research.search, value)
        except Exception as exc:
            audit("product_search_failed", error=type(exc).__name__, requester_id=requester_id)
            return "⚠️ Pencarian marketplace gagal saat ini. Belum ada harga atau rekomendasi yang bisa gue verifikasi; coba lagi nanti."
        audit("product_search_completed", result_count=len(results), requester_id=requester_id)
        if not results:
            return "🔎 Gue belum menemukan listing marketplace yang cocok di hasil pencarian. Coba sebutkan merek/model atau kata kunci yang lebih spesifik."
        context = product_research.model_context(value, results)
        reply, provider = await asyncio.to_thread(
            llm_chat.chat, [{"role": "user", "content": value}], context,
        )
        audit("product_search_analysis", provider=provider or "unavailable", requester_id=requester_id)
        if reply is None:
            reply = "Gue menemukan beberapa listing, tapi provider AI sedang tidak tersedia untuk membandingkannya. Harga di hasil indeks belum tentu terbaru; cek listing langsung."
        return reply + "\n\n**Sumber listing**\n" + product_research.source_footer(results)
    if kind == "laptop_status":
        if requester_id is None or not is_mikrotik_owner(requester_id):
            audit("laptop_readonly_access_denied", requester_id=requester_id)
            return "🔒 Informasi dan diagnostik laptop hanya bisa diakses Henka."
        result = await asyncio.to_thread(local_runner.diagnose, value or "system")
        audit("local_readonly", target=value)
        return format_local_diagnostic(value, result)
    if kind == "provider_status":
        statuses = await asyncio.to_thread(llm_chat.provider_status)
        audit("provider_status", result=statuses)
        return "🔌 **Status provider AI**\n" + "\n".join(f"- `{name}`: {'✅' if status == 'VALID' else '⚠️'} **{status}**" for name, status in statuses.items()) + "\n\n🔒 Nilai token tidak pernah ditampilkan."
    if kind == "runtime_info":
        response = await asyncio.to_thread(runtime_info.render)
        audit("runtime_info_read", requester_id=requester_id)
        return response
    if kind == "stock_lookup":
        try:
            result = await asyncio.to_thread(stock_market.analyze, value)
            audit("stock_lookup", symbol=result["symbol"], exchange=result["exchange"], requester_id=requester_id)
            return stock_market.render(result)
        except Exception as exc:
            audit("stock_lookup_error", symbol=value, error=type(exc).__name__, requester_id=requester_id)
            if not os.getenv("API_CO_ID_KEY", "").strip():
                return "📈 Data saham belum aktif. Buat API key gratis Api.co.id, lalu simpan sebagai `API_CO_ID_KEY` di `.env` lokal—jangan kirim key ke Discord."
            return "⚠️ Gue belum bisa memperoleh histori harga saham itu. Cek kode saham atau ketersediaan datanya, lalu coba lagi."
    if kind == "stock_average":
        try:
            from datetime import date, timedelta
            end_date = date.today().isoformat()
            start_date = (date.today() - timedelta(days=366)).isoformat()
            result = await asyncio.to_thread(stock_market.analyze, value, start_date, end_date)
            audit("stock_average", symbol=result["symbol"], observations=result["observations"], requester_id=requester_id)
            return stock_market.render(result)
        except Exception as exc:
            audit("stock_average_error", symbol=value, error=type(exc).__name__, requester_id=requester_id)
            return "⚠️ Belum bisa menghitung rata-rata historis. Pastikan `API_CO_ID_KEY` tersedia dan coba lagi."
    if kind == "stock_compare":
        try:
            symbol, start_date, end_date = value.split("|", 2)
            from datetime import date, timedelta
            if date.fromisoformat(start_date) > date.fromisoformat(end_date):
                return "Tanggal awal harus sebelum atau sama dengan tanggal akhir."
            start_from = (date.fromisoformat(start_date) - timedelta(days=10)).isoformat()
            end_from = (date.fromisoformat(end_date) - timedelta(days=10)).isoformat()
            start_data = await asyncio.to_thread(stock_market.analyze, symbol, start_from, start_date)
            end_data = await asyncio.to_thread(stock_market.analyze, symbol, end_from, end_date)
            audit("stock_compare", symbol=symbol, start_date=start_date, end_date=end_date, requester_id=requester_id)
            return stock_market.render_comparison(symbol, start_data, end_data)
        except Exception as exc:
            audit("stock_compare_error", error=type(exc).__name__, requester_id=requester_id)
            return "⚠️ Belum bisa membandingkan tanggal itu. Pastikan format `YYYY-MM-DD`, tanggalnya punya data sesi bursa, dan `API_CO_ID_KEY` sudah diatur."
    if kind == "local_action_proposal":
        if requester_id is None:
            return "Gunakan channel command untuk meminta aksi lokal agar approval bisa dikaitkan ke user yang meminta."
        if not can_request_local_action(requester_id):
            return "🔒 Aksi sistem laptop hanya bisa diminta operator laptop yang terdaftar. Diagnostik read-only tetap tersedia."
        try:
            record = await asyncio.to_thread(local_gate.propose, requester_id, value)
        except Exception as exc:
            return f"⛔ Aksi lokal ditolak: {exc}"
        audit("local_approval_created", approval_id=record["id"], action=value, requester_id=requester_id)
        return (f"**Local approval `{record['id']}`**\nAksi: `{value}`\n"
                "Belum dijalankan. Berlaku 10 menit. Konfirmasi dengan "
                f"`/approved approval_id:{record['id']}`.")
    if kind == "engineering_security_task":
        title = "Security audit — ERP Henka"
        description = (
            "Lakukan security audit read-only terhadap source repository ERP Henka yang "
            "sudah dikonfigurasi untuk Hermes. Kumpulkan bukti berupa file/baris atau konfigurasi "
            "yang relevan; prioritaskan autentikasi dan otorisasi, paparan secrets, validasi input, "
            "CORS, rate limiting pada login/API sensitif, security headers, logging data sensitif, "
            "serta dependency/runtime yang usang. Laporkan temuan dengan severity, dampak nyata, "
            "bukti, dan rekomendasi perbaikan. Jangan mengubah file, memasang/memperbarui paket, "
            "menguji endpoint produksi secara agresif, merge, atau deploy. Jika source repo belum "
            "tersedia, catat sebagai blocker dan jangan mengarang hasil."
        )
        try:
            record = await asyncio.to_thread(
                engineering_tasks.create, title, description,
                f"Permintaan Discord dari {requester_id or 'unknown'}: {text}",
            )
        except Exception as exc:
            audit("engineering_task_create_failed", task_type="security_audit", error=type(exc).__name__)
            return "⚠️ Gue belum berhasil menyimpan task audit. Coba `/eng-task`; tidak ada audit atau perubahan yang dijalankan."
        audit("engineering_task_created", task_id=record["id"], task_type="security_audit", requester_id=requester_id)
        return (
            f"🛡️ Task security audit ERP Henka dibuat: `{record['id']}` (status **OPEN**).\n"
            "Scope-nya read-only: cari bukti dan rangkum severity/dampak/rekomendasi. "
            "Belum ada file, paket, atau server yang diubah. Codex bisa mengambil task ini dari:\n"
            f"`data/engineering/tasks/{record['id']}.md`"
        )
    if kind == "template":
        audit("form_template", command_length=len(text))
        return command_templates.match(value) or "Template belum tersedia untuk alur itu."
    if kind == "stock_latest":
        audit("readonly_command", target="stock_latest")
        return await run_readonly("stock_latest")
    if kind == "help":
        return help_text()
    if kind == "status":
        audit("readonly_command", target=value)
        return await run_readonly("status", value or "all")
    if kind == "isp_status":
        if requester_id is None or not is_mikrotik_owner(requester_id):
            audit("mikrotik_access_denied", requester_id=requester_id, kind=kind, target=value)
            return "🔒 Data MikroTik hanya bisa dicek oleh Henka."
        snapshot = await asyncio.to_thread(monitor.collect, False)
        system = snapshot.get("systems", {}).get("core_gateway")
        audit("mikrotik_isp_status", requester_id=requester_id, result_status=(system or {}).get("status", "UNKNOWN"))
        return network_health.render_isp_status(system)
    if kind == "diff":
        audit("readonly_command", target="diff")
        return await run_readonly("diff")
    if kind == "mutation_blocked":
        audit("mutation_blocked", command_length=len(text))
        return ("⛔ Tindakan perubahan belum diaktifkan. Saya tidak akan menebak target atau "
                "menjalankan konfigurasi. Gunakan perintah read-only, atau daftarkan executor "
                "spesifik setelah target, dampak, dan rollback ditentukan.")
    return "Perintah belum dikenali. Ketik `/help` untuk daftar perintah yang tersedia."


WORKER_DISPATCHER.register("command", _dispatch_text)


async def dispatch_text(text: str, requester_id: int | None = None) -> str:
    """Dispatch a command through the serialized worker boundary."""
    return await WORKER_DISPATCHER.dispatch("command", text, requester_id)


def chat_context(query: str, requester_id: int | None = None, channel_id: int | None = None) -> str:
    technical = knowledge_base.retrieve(query)
    official = official_sources.context_for(query)
    curated = curated_knowledge.context_for(query)
    persistent = ""
    if requester_id is not None and channel_id is not None:
        persistent = memory_store.context(
            owner_id=requester_id,
            channel_id=channel_id,
            query=query,
            project_key=memory_store.infer_project(query),
        )
    return (
        runtime_info.context_facts() + "\n\n" +
        "Agent monitor lokal ini dibuat dengan Python, terutama monitor.py untuk collector/baseline, "
        "routeros_probe.py untuk probe MikroTik, discord_bot.py untuk Discord, dan llm_chat.py untuk percakapan. "
        "Ini tidak membuktikan bahasa/framework source code ERP Henka itu sendiri.\n" +
        (persistent + "\n" if persistent else "") +
        ("Knowledge/runbook teknis yang relevan:\n" + technical + "\n" if technical else "") +
        (official + "\n" if official else "") +
        (curated + "\n" if curated else "") +
        repo_context.search(query)
    )


async def persist_auto_summary(channel_id: int, requester_id: int, history: list[dict[str, str]]) -> None:
    """Persist a redacted conversation summary without delaying the Discord reply."""
    try:
        turn_count, due = await asyncio.to_thread(memory_store.record_turn, requester_id, channel_id)
        if not due:
            return
        redacted_history = []
        for item in history[-memory_store.SHORT_TERM_LIMIT:]:
            content, _ = memory_store.redact(item.get("content", ""))
            redacted_history.append({"role": item.get("role", "user"), "content": content})
        query = next((item["content"] for item in reversed(redacted_history) if item.get("role") == "user"), "")
        context = await asyncio.to_thread(chat_context, query, requester_id, channel_id)
        summary, provider = await asyncio.to_thread(llm_chat.summarize, redacted_history, context)
        if not summary:
            summary = "Percakapan penting: " + " | ".join(
                f"{item['role']}: {item['content'][:350]}" for item in redacted_history[-6:]
            )
            provider = "deterministic-fallback"
        project_key = memory_store.infer_project(" ".join(item["content"] for item in redacted_history))
        record = await asyncio.to_thread(
            memory_store.save_summary,
            owner_id=requester_id,
            channel_id=channel_id,
            content=summary,
            project_key=project_key,
            turn_end=turn_count,
        )
        audit("memory_auto_summary", summary_id=record["id"], provider=provider or "unavailable", requester_id=requester_id)
    except Exception as exc:
        audit("memory_auto_summary_failed", error=type(exc).__name__, requester_id=requester_id)


async def natural_reply(channel_id: int, text: str, requester_id: int | None = None, requester_name: str = "") -> str:
    current_identity = {"name": requester_name or "Pengguna Discord", "username": ""}
    if identity.asks_current_identity(text):
        return identity.answer(current_identity)
    # Resolve short answers to the previous model clarification deterministically
    # so "semuanya" means all requested RAM sources, not a generic capacity essay.
    history = conversation_history.get((channel_id, requester_id or 0), [])
    if normalize_command(text) in {"semua", "semuanya", "semua sumber", "ketiganya"} and history:
        previous_assistant = next((item["content"].lower() for item in reversed(history) if item.get("role") == "assistant"), "")
        if "ram" in previous_assistant and any(word in previous_assistant for word in ("server", "laptop", "monitor-agent")):
            return await dispatch_text("cek pemakaian RAM semua", requester_id)
    if requester_id is not None and command_templates.match(text):
        catalog = None
        try:
            parsed = form_parser.parse(text)
        except ValueError as exc:
            # Name/ID resolution for existing ERP records uses a fresh read-only
            # catalog. Only completed write templates reach this path.
            try:
                snapshot = await asyncio.to_thread(monitor.collect, False)
                erp = snapshot.get("systems", {}).get("kasapink", {})
                state = erp.get("state", {}) if erp.get("status") == "OK" else {}
                catalog = state if isinstance(state, dict) else None
                parsed = form_parser.parse(text, state)
            except ValueError as catalog_exc:
                return f"⛔ Isian belum bisa diproses: {catalog_exc}"
            except Exception as catalog_exc:
                audit("form_catalog_read_failed", error=type(catalog_exc).__name__, requester_id=requester_id)
                return "⛔ Gue belum bisa membaca katalog ERP terbaru. Belum ada perubahan yang dibuat; coba lagi setelah ERP pulih."
        if parsed:
            if not can_write_erp(requester_id):
                audit("erp_write_access_denied", requester_id=requester_id, endpoint=parsed[1])
                return "🔒 Akun lu bisa ngobrol dan cek data, tapi belum mendapat hak operator ERP. Minta Henka mengatur akses operator dulu."
            method, path, body = parsed
            if catalog is None:
                try:
                    snapshot = await asyncio.to_thread(monitor.collect, False)
                    erp = snapshot.get("systems", {}).get("kasapink", {})
                    if erp.get("status") == "OK" and isinstance(erp.get("state"), dict):
                        catalog = erp["state"]
                except Exception as catalog_exc:
                    audit("auto_erp_catalog_failed", error=type(catalog_exc).__name__, requester_id=requester_id)
            auto_allowed, policy_reason = erp_auto_policy.evaluate(
                method, path, body, form_parser.extract_fields(text), catalog,
            )
            if auto_allowed:
                try:
                    record, _ = await asyncio.to_thread(
                        approval.execute_automatically,
                        requester_id=requester_id, requester_name=requester_name,
                        method=method, path=path, body=body, policy_reason=policy_reason,
                    )
                except approval.AutomaticExecutionError as exc:
                    audit("auto_erp_execution_unknown", approval_id=exc.approval_id, path=path, requester_id=requester_id)
                    return (f"⚠️ Hasil pencatatan ERP belum bisa dipastikan (referensi `{exc.approval_id}`). "
                            "Jangan kirim ulang dulu—cek ERP secara read-only untuk memastikan apakah data sudah masuk.")
                except Exception as exc:
                    audit("auto_erp_execution_failed", path=path, error=type(exc).__name__, requester_id=requester_id)
                    return f"⛔ ERP menolak pencatatan otomatis ({type(exc).__name__}); tidak ada percobaan ulang. Minta approval manual."
                audit("auto_erp_execution_succeeded", approval_id=record["id"], path=path, requester_id=requester_id, policy_reason=policy_reason)
                return (f"✅ **Berhasil dicatat otomatis di ERP Henka**\n"
                        f"Jenis: `{path.rsplit('/', 1)[-1]}` · Referensi: `{record['id']}`\n"
                        f"Validasi: {policy_reason}. Tidak perlu `/approve`.")
            record = approval.create(
                requester_id=requester_id, requester_name=requester_name,
                method=method, path=path, body=body,
            )
            audit("approval_created_from_form", approval_id=record["id"], method=method, path=path, requester_id=requester_id)
            return approval.describe(record) + f"\n\n🛡️ Perlu approval manual: {policy_reason}."
    kind, value = classify_command(text)
    if requester_id is not None and not can_access_mikrotik(requester_id, kind, value):
        audit("mikrotik_access_denied", requester_id=requester_id, kind=kind, target=value)
        return "🔒 Data MikroTik hanya bisa dicek oleh Henka."
    if kind != "unknown":
        return await dispatch_text(text, requester_id)
    history_key = (channel_id, requester_id or 0)
    history = conversation_history.setdefault(history_key, [])
    context = await asyncio.to_thread(chat_context, text, requester_id, channel_id)
    context += "\n" + identity.context_line(current_identity)
    history.append({"role": "user", "content": text})
    reply, provider = await asyncio.to_thread(llm_chat.chat, history, context)
    if reply is None:
        history.pop()
        fallback = await asyncio.to_thread(llm_chat.deterministic_fallback, [{"role": "user", "content": text}])
        if fallback:
            audit("chat", provider="deterministic-fallback")
            return fallback
        configured = await asyncio.to_thread(llm_chat.configured_providers)
        if configured:
            return ("⚠️ Provider AI sudah terkonfigurasi (" + ", ".join(configured) + "), "
                    "tetapi sedang tidak dapat dihubungi. Hermes tetap bisa menjalankan "
                    "perintah deterministik/read-only; fitur chat bebas dan ringkasan AI "
                    "bisa gagal sampai koneksi provider pulih.")
        return ("⚠️ Provider AI belum terkonfigurasi di agent lokal. Isi `GROQ_API_KEY` "
                "atau `GOOGLE_API_KEY` di `.env` lokal; token tidak perlu dikirim ke chat.")
    history.append({"role": "assistant", "content": reply})
    if len(history) > memory_store.SHORT_TERM_LIMIT:
        del history[:-memory_store.SHORT_TERM_LIMIT]
    if requester_id is not None:
        asyncio.create_task(persist_auto_summary(channel_id, requester_id, list(history)))
    audit("chat", provider=provider)
    return reply


async def send_command_result(interaction: discord.Interaction, text: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    kind, target = classify_command(text)
    if not can_access_mikrotik(interaction.user.id, kind, target):
        await interaction.response.send_message("🔒 Data MikroTik hanya bisa dicek oleh Henka.", ephemeral=True)
        return
    await interaction.response.defer()
    current_user = identity.profile(interaction.user)
    response = await safe_natural_reply(interaction.channel_id, text, interaction.user.id, current_user["name"])
    for chunk in split_discord_message(response):
        await interaction.followup.send(chunk, allowed_mentions=discord.AllowedMentions.none())


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception) -> None:
    """Keep one failed interaction from becoming an unhandled Discord error."""
    command_name = getattr(getattr(interaction, "command", None), "name", "unknown")
    audit("app_command_error", command=command_name, error=type(error).__name__)
    message = "⚠️ Hermes gagal menyelesaikan perintah ini. Error sudah dicatat; coba lagi setelah koneksi stabil."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as followup_error:
        audit("app_command_error_reply_failed", command=command_name, error=type(followup_error).__name__)


@bot.event
async def on_error(event_method: str, *args: object, **kwargs: object) -> None:
    """Audit event failures without exposing message contents or credentials."""
    audit("discord_event_error", event=event_method)


def parse_json_body(raw: str) -> dict:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON body tidak valid: {exc.msg}") from exc
    if not isinstance(body, dict):
        raise ValueError("body harus berupa JSON object")
    return body


async def propose_action(interaction: discord.Interaction, method: str, endpoint: str, body: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    if not can_write_erp(interaction.user.id):
        await interaction.response.send_message("🔒 Akun ini belum diberi hak operator ERP.", ephemeral=True)
        return
    try:
        record = approval.create(
            requester_id=interaction.user.id, requester_name=str(interaction.user),
            method=method, path=endpoint, body=parse_json_body(body)
        )
    except (ValueError, RuntimeError) as exc:
        await interaction.response.send_message(f"⛔ Proposal ditolak: {exc}", ephemeral=True)
        return
    audit("approval_created", approval_id=record["id"], method=record["method"], path=record["path"], requester_id=interaction.user.id)
    await interaction.response.send_message(approval.describe(record))


async def approve_action(interaction: discord.Interaction, approval_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    if not can_write_erp(interaction.user.id):
        await interaction.response.send_message("🔒 Approval ERP hanya bisa dijalankan operator ERP.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        record, result = await asyncio.to_thread(approval.approve_and_execute, approval_id, interaction.user.id)
        audit("approval_executed", approval_id=record["id"], method=record["method"], path=record["path"], approver_id=interaction.user.id)
        rendered = json.dumps(result, ensure_ascii=False)[:1500]
        await interaction.followup.send(f"✅ Perubahan berhasil dijalankan untuk `{record['path']}`. Hasil ERP: `{rendered}`")
    except Exception as exc:
        audit("approval_failed", approval_id=approval_id, error=type(exc).__name__, approver_id=interaction.user.id)
        record = approval._read().get(approval_id.upper(), {})
        if record.get("status") == "UNKNOWN":
            await interaction.followup.send(
                "⚠️ Hasil ERP belum bisa dipastikan karena koneksi terputus/timeout. Jangan approve ulang dulu; "
                "cek data ERP secara read-only untuk memastikan apakah perubahan sudah tercatat. Approval ini dikunci agar tidak terkirim dua kali."
            )
        else:
            await interaction.followup.send(f"⛔ Perubahan tidak dijalankan: {exc}")


async def deny_action(interaction: discord.Interaction, approval_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    if not can_write_erp(interaction.user.id):
        await interaction.response.send_message("🔒 Approval ERP hanya bisa dikelola operator ERP.", ephemeral=True)
        return
    try:
        record = approval.deny(approval_id, interaction.user.id)
        audit("approval_denied", approval_id=record["id"], requester_id=interaction.user.id)
        await interaction.response.send_message(f"✅ Approval `{record['id']}` dibatalkan.")
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Tidak bisa membatalkan approval: {exc}", ephemeral=True)


@tree.command(name="approval-status", description="Lihat status proposal ERP milik lu tanpa mengulang eksekusi")
@app_commands.describe(approval_id="ID HMR-...")
async def approval_status(interaction: discord.Interaction, approval_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    if not can_write_erp(interaction.user.id):
        await interaction.response.send_message("🔒 Status proposal ERP hanya tersedia untuk operator ERP.", ephemeral=True)
        return
    try:
        record = approval.get_status(approval_id, interaction.user.id)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Status tidak bisa dibaca: {exc}", ephemeral=True)
        return
    lines = [
        f"🧾 **Proposal `{record['id']}` — {record['status']}**",
        f"Target: `{record['path']}` · Metode: `{record['method']}`",
    ]
    if record["status"] == "UNKNOWN":
        lines.append("⚠️ Jangan kirim ulang. Cek ERP secara read-only untuk memastikan perubahan masuk; Hermes sengaja mengunci eksekusi ulang.")
    elif record["status"] == "PENDING":
        lines.append("Belum dijalankan. Periksa detail proposal di pesan awal sebelum `/approve` atau `/deny`.")
    elif record["status"] == "EXECUTED":
        lines.append("✅ ERP mengonfirmasi permintaan selesai. Ini bukan mekanisme rollback.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@tasks.loop(minutes=INTERVAL_MINUTES)
async def monitor_loop() -> None:
    if not alerts_enabled():
        return
    try:
        event, snapshot, anomalies, changes = await asyncio.to_thread(evaluate_snapshot)
    except Exception as exc:
        audit("monitor_cycle_error", error=type(exc).__name__)
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        fingerprint = f"monitor-cycle-error:{type(exc).__name__}"
        if channel and hasattr(channel, "send") and await asyncio.to_thread(alert_dedup.claim, fingerprint):
            try:
                await channel.send(f"🚨 Hermes gagal menyelesaikan siklus monitoring ({type(exc).__name__}). Bot tetap berjalan dan akan mencoba lagi pada siklus berikutnya.", allowed_mentions=discord.AllowedMentions.none())
            except Exception as send_exc:
                audit("monitor_error_alert_failed", error=type(send_exc).__name__)
        else:
            audit("monitor_alert_suppressed", alert_event="CYCLE_ERROR", fingerprint=fingerprint)
        return
    if event not in {"ERROR", "RECOVERY", "CHANGE_DETECTED"} and not anomalies:
        # Normal health is reported by the once-daily digest, not this loop.
        return
    channel = bot.get_channel(ALERT_CHANNEL_ID)
    fingerprint = alert_dedup.fingerprint(event, snapshot, anomalies, changes)
    if channel and hasattr(channel, "send") and await asyncio.to_thread(alert_dedup.claim, fingerprint):
        message = alert_text(event, snapshot, changes)
        if anomalies:
            message += "\n\n" + anomaly_rules.render(anomalies)
        try:
            await channel.send(message[:1900], allowed_mentions=discord.AllowedMentions.none())
        except Exception as send_exc:
            audit("monitor_alert_delivery_failed", event=event, error=type(send_exc).__name__)
    else:
        audit("monitor_alert_suppressed", alert_event=event, fingerprint=fingerprint)


def _claim_daily_report(report_date: str) -> bool:
    with exclusive(DAILY_REPORT_FILE):
        try:
            state = json.loads(DAILY_REPORT_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        if state.get("date") == report_date and state.get("status") in {"SENDING", "SENT", "UNKNOWN"}:
            return False
        state = {"date": report_date, "status": "SENDING", "started_at": datetime.now(JAKARTA_TZ).isoformat()}
        DAILY_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = DAILY_REPORT_FILE.with_suffix(DAILY_REPORT_FILE.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(DAILY_REPORT_FILE)
        return True


def _finish_daily_report(report_date: str, status: str) -> None:
    with exclusive(DAILY_REPORT_FILE):
        state = {"date": report_date, "status": status, "finished_at": datetime.now(JAKARTA_TZ).isoformat()}
        DAILY_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = DAILY_REPORT_FILE.with_suffix(DAILY_REPORT_FILE.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(DAILY_REPORT_FILE)


def _claim_stock_report(report_date: str, session: str) -> bool:
    with exclusive(STOCK_REPORT_FILE):
        try:
            state = json.loads(STOCK_REPORT_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        if state.get("date") != report_date:
            state = {"date": report_date, "sessions": {}}
        sessions = state.setdefault("sessions", {})
        if sessions.get(session) in {"SENDING", "SENT", "UNKNOWN", "NO_MARKET_DATA"}:
            return False
        sessions[session] = "SENDING"
        state["updated_at"] = datetime.now(JAKARTA_TZ).isoformat()
        STOCK_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = STOCK_REPORT_FILE.with_suffix(STOCK_REPORT_FILE.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(STOCK_REPORT_FILE)
        return True


def _finish_stock_report(report_date: str, session: str, status: str) -> None:
    with exclusive(STOCK_REPORT_FILE):
        try:
            state = json.loads(STOCK_REPORT_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            state = {"date": report_date, "sessions": {}}
        if state.get("date") != report_date:
            state = {"date": report_date, "sessions": {}}
        state.setdefault("sessions", {})[session] = status
        state["updated_at"] = datetime.now(JAKARTA_TZ).isoformat()
        STOCK_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = STOCK_REPORT_FILE.with_suffix(STOCK_REPORT_FILE.suffix + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(STOCK_REPORT_FILE)


async def send_daily_report() -> None:
    if not alerts_enabled():
        audit("daily_report_skipped_alerts_disabled")
        return
    report_date = datetime.now(JAKARTA_TZ).date().isoformat()
    if not await asyncio.to_thread(_claim_daily_report, report_date):
        return
    try:
        snapshot = await asyncio.to_thread(monitor.collect, False)
        anomaly_since = (datetime.now(JAKARTA_TZ) - timedelta(hours=24)).isoformat()
        recent_anomalies = await asyncio.to_thread(history_store.recent_anomalies, anomaly_since, 10)
        message = daily_report.render(snapshot, recent_anomalies=recent_anomalies)
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(ALERT_CHANNEL_ID)
        for chunk in split_discord_message(message, 1850):
            await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        await asyncio.to_thread(_finish_daily_report, report_date, "SENT")
        audit("daily_report_sent", date=report_date)
    except Exception as exc:
        # Delivery may have reached Discord even when the acknowledgement was lost;
        # do not automatically blast the same report twice on an uncertain result.
        await asyncio.to_thread(_finish_daily_report, report_date, "UNKNOWN")
        audit("daily_report_delivery_unknown", date=report_date, error=type(exc).__name__)


def stock_session_for_time(now: datetime) -> str | None:
    """Return the IDX session to recap at its scheduled post-session time."""
    if now.weekday() > 4:
        return None
    if now.hour == 16 and now.minute == 20:
        return "close"
    if now.weekday() == 4 and now.hour == 11 and now.minute == 35:
        return "morning"
    if now.weekday() < 4 and now.hour == 12 and now.minute == 5:
        return "morning"
    return None


async def send_stock_session_report(session: str) -> None:
    report_date = datetime.now(JAKARTA_TZ).date().isoformat()
    if STOCK_CHANNEL_ID is None:
        audit("stock_report_skipped_channel_missing", date=report_date, session=session)
        return
    if not await asyncio.to_thread(_claim_stock_report, report_date, session):
        return
    try:
        results, ihsg = await asyncio.gather(
            asyncio.to_thread(daily_report.fetch_stock_sample),
            asyncio.to_thread(daily_report.fetch_ihsg),
        )
        fresh = [(symbol, result) for symbol, result in results if result and result.get("date") == report_date]
        if not fresh:
            await asyncio.to_thread(_finish_stock_report, report_date, session, "NO_MARKET_DATA")
            audit("stock_report_skipped_no_current_session_data", date=report_date, session=session)
            return
        channel = bot.get_channel(STOCK_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(STOCK_CHANNEL_ID)
        message = daily_report.render_stock(fresh, session=session, ihsg=ihsg)
        for chunk in split_discord_message(message, 1850):
            await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        await asyncio.to_thread(_finish_stock_report, report_date, session, "SENT")
        audit("stock_session_report_sent", date=report_date, session=session, tickers=len(fresh))
    except Exception as exc:
        await asyncio.to_thread(_finish_stock_report, report_date, session, "UNKNOWN")
        audit("stock_session_report_delivery_unknown", date=report_date, session=session, error=type(exc).__name__)


@tasks.loop(time=clock_time(hour=6, minute=0, tzinfo=JAKARTA_TZ))
async def daily_report_loop() -> None:
    await send_daily_report()


@tasks.loop(time=[clock_time(11, 35, tzinfo=JAKARTA_TZ), clock_time(12, 5, tzinfo=JAKARTA_TZ), clock_time(16, 20, tzinfo=JAKARTA_TZ)])
async def stock_report_loop() -> None:
    session = stock_session_for_time(datetime.now(JAKARTA_TZ))
    if session:
        await send_stock_session_report(session)


@tasks.loop(minutes=1)
async def job_schedule_loop() -> None:
    try:
        records = await asyncio.to_thread(job_executor.run_due_schedules, datetime.now(JAKARTA_TZ))
        if records:
            channel = bot.get_channel(COMMAND_CHANNEL_ID)
            if channel:
                for record in records:
                    await channel.send(format_job(record), allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        audit("job_schedule_loop_failed", error=type(exc).__name__)


@tasks.loop(seconds=10)
async def restart_notification_loop() -> None:
    """Confirm approved Hermes/ChatGPT restarts in the alert channel."""
    if not alerts_enabled():
        return
    try:
        pending = await asyncio.to_thread(local_runner.pending_restart_notifications)
        if not pending:
            return
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if not channel or not hasattr(channel, "send"):
            return
        now = datetime.now(timezone.utc).timestamp()
        for item in pending:
            action = str(item.get("action", ""))
            if action in {"start_chatgpt", "restart_chatgpt"}:
                status = await asyncio.to_thread(local_runner.chatgpt_status)
                ready = status.get("status") == "RUNNING"
                subject = "ChatGPT/Codex"
                evidence = f"{status.get('responding_count', 0)}/{status.get('process_count', 0)} proses responsif"
            elif action == "restart_hermes":
                status = await asyncio.to_thread(local_runner.diagnose, "hermes")
                ready = str(status.get("task_scheduler", "")).strip().lower() == "running" and bot.is_ready()
                subject = "Hermes"
                evidence = "Bot Discord siap menerima command"
            else:
                continue
            if ready:
                await channel.send(
                    f"✅ **{subject} kembali online** setelah restart.\n🔎 Bukti: {evidence}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await asyncio.to_thread(local_runner.mark_restart_notification, item["id"], "SENT", evidence)
            elif now >= float(item.get("expires_at", now)):
                await channel.send(
                    f"⚠️ **{subject} belum terverifikasi online** setelah restart dalam 3 menit.\n"
                    "Gunakan `/laptop-check` untuk pemeriksaan lanjutan.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await asyncio.to_thread(local_runner.mark_restart_notification, item["id"], "TIMEOUT", "status belum RUNNING")
    except Exception as exc:
        audit("restart_notification_loop_failed", error=type(exc).__name__)


@monitor_loop.before_loop
async def before_monitor_loop() -> None:
    await bot.wait_until_ready()


@daily_report_loop.before_loop
async def before_daily_report_loop() -> None:
    await bot.wait_until_ready()


@stock_report_loop.before_loop
async def before_stock_report_loop() -> None:
    await bot.wait_until_ready()


@job_schedule_loop.before_loop
async def before_job_schedule_loop() -> None:
    await bot.wait_until_ready()


@restart_notification_loop.before_loop
async def before_restart_notification_loop() -> None:
    await bot.wait_until_ready()


async def send_channel_test_once() -> None:
    """Send one explicitly armed startup test to cached Hermes channels only."""
    if not alerts_enabled():
        return
    if not CHANNEL_TEST_MARKER.exists():
        return
    try:
        # Delete before sending so reconnects and partial failures never repeat the blast.
        CHANNEL_TEST_MARKER.unlink()
        guild = bot.get_guild(int(GUILD_ID)) if GUILD_ID.isdigit() else None
        if guild is None:
            audit("channel_test_unavailable", reason="guild_not_cached")
            return
        channels = sorted(
            (channel for channel in guild.text_channels if channel.name.startswith("hermes-")),
            key=lambda channel: channel.name,
        )
        if not channels:
            audit("channel_test_unavailable", reason="no_hermes_text_channels")
            return
        content = (
            f"🧪 **Tes channel Hermes — Bossun** · {datetime.now(JAKARTA_TZ):%d %b %Y %H:%M WIB}\n"
            "Bot berhasil mengirim pesan ke channel ini. Tes koneksi saja; tidak perlu tindakan."
        )
        results = []
        for channel in channels:
            try:
                await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                results.append({"channel": channel.name, "result": "sent"})
            except Exception as exc:
                results.append({"channel": channel.name, "result": "failed", "error": type(exc).__name__})
        audit("channel_test_completed", results=results)
    except Exception as exc:
        audit("channel_test_failed", error=type(exc).__name__)


@bot.event
async def on_ready() -> None:
    if GUILD_ID.isdigit():
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    await send_channel_test_once()
    if not monitor_loop.is_running():
        monitor_loop.start()
    if not daily_report_loop.is_running():
        daily_report_loop.start()
    if not stock_report_loop.is_running():
        stock_report_loop.start()
    if not job_schedule_loop.is_running():
        job_schedule_loop.start()
    if not restart_notification_loop.is_running():
        restart_notification_loop.start()
    if datetime.now(JAKARTA_TZ).hour >= 6:
        asyncio.create_task(send_daily_report())
    print(f"Discord bot online sebagai {bot.user}")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    allowed = permitted_message(message)
    audit(
        "message_received",
        channel_id=message.channel.id,
        author_id=message.author.id,
        content_length=len(message.content or ""),
        permitted=allowed,
    )
    if not allowed:
        return
    accepted = await asyncio.to_thread(
        message_dedup.claim,
        message.id,
        message.channel.id,
        message.author.id,
        message.content or "",
    )
    if not accepted:
        audit(
            "message_duplicate_suppressed",
            channel_id=message.channel.id,
            author_id=message.author.id,
            content_length=len(message.content or ""),
        )
        return
    current_user = identity.profile(message.author)
    response = await safe_natural_reply(message.channel.id, message.content, message.author.id, current_user["name"])
    chunks = split_discord_message(response)
    await message.reply(chunks[0], mention_author=False, allowed_mentions=discord.AllowedMentions.none())
    for chunk in chunks[1:]:
        await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


@tree.command(name="help", description="Daftar perintah Hermes")
async def help_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    # A slash command must acknowledge within Discord's short interaction
    # window. Keep /help to one REST response; follow-ups are more fragile
    # when the Discord API connection is being interrupted.
    # Keep the first response below Discord's UTF-16 content limit.
    await interaction.response.send_message(split_discord_message(help_text(), limit=1900)[0], ephemeral=True)


@tree.command(name="business", description="Tanya analisis bisnis berdasarkan data ERP read-only")
@app_commands.describe(question="Contoh: jual nasi bakar Rp15.000 masih untung nggak?")
async def business_command(interaction: discord.Interaction, question: str) -> None:
    await send_command_result(interaction, question)


@tree.command(name="business-report", description="Ringkasan finance dan F&B ERP read-only")
@app_commands.describe(start_date="Tanggal awal YYYY-MM-DD, default 7 hari terakhir", end_date="Tanggal akhir YYYY-MM-DD, default hari ini")
async def business_report_command(interaction: discord.Interaction, start_date: str = "", end_date: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(business_advisor.report, start_date or None, end_date or None)
        audit("business_report_read", start_date=result["start_date"], end_date=result["end_date"], requester_id=interaction.user.id)
        await interaction.followup.send(business_advisor.render_report(result), ephemeral=True)
    except ValueError as exc:
        await interaction.followup.send(f"⛔ Periode laporan tidak valid: {str(exc)[:300]}", ephemeral=True)
    except Exception as exc:
        audit("business_report_failed", error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send("⚠️ Laporan bisnis belum bisa mengambil data ERP. Tidak ada data yang diubah; coba lagi setelah ERP siap.", ephemeral=True)


@tree.command(name="fnb", description="Diskusi F&B, operasi dapur, costing, stok, dan menu")
@app_commands.describe(question="Contoh: cara mengurangi waste nasi atau menghitung food cost")
async def fnb_command(interaction: discord.Interaction, question: str) -> None:
    await send_command_result(interaction, f"F&B: {question}")


@tree.command(name="parfum", description="Diskusi notes dan rekomendasi parfum")
@app_commands.describe(question="Contoh: notes parfum yang cocok untuk siang dan cuaca panas")
async def parfum_command(interaction: discord.Interaction, question: str) -> None:
    await send_command_result(interaction, f"Parfum: {question}")


@tree.command(name="sources", description="Lihat sumber resmi knowledge Hermes dan jadwal review")
@app_commands.describe(topic="Contoh: F&B, food safety, parfum, IFRA, atau BPOM; kosongkan untuk semua")
async def sources_command(interaction: discord.Interaction, topic: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    audit("official_sources_viewed", requester_id=interaction.user.id, topic=topic[:120])
    await interaction.response.send_message(official_sources.render(topic), ephemeral=True)


@tree.command(name="knowledge-sources", description="Lihat peta knowledge yang diberikan owner Hermes")
@app_commands.describe(topic="Contoh: Docker, MikroTik, Python, F&B, atau parfum; kosongkan untuk semua")
async def knowledge_sources_command(interaction: discord.Interaction, topic: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    audit("curated_knowledge_viewed", requester_id=interaction.user.id, topic=topic[:120])
    await interaction.response.send_message(curated_knowledge.render(topic), ephemeral=True)


@tree.command(name="whoami", description="Lihat siapa yang sedang dikenali Hermes")
async def whoami_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    current_user = identity.profile(interaction.user)
    audit("identity_whoami", requester_id=interaction.user.id)
    await interaction.response.send_message(identity.answer(current_user), ephemeral=True)


@tree.command(name="users", description="Lihat user yang sudah command ke Hermes")
async def users_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    rows = await asyncio.to_thread(memory_store.list_users)
    audit("identity_users_listed", result_count=len(rows), requester_id=interaction.user.id)
    await interaction.response.send_message(identity.render_users(rows), ephemeral=True)


@tree.command(name="remember", description="Simpan fakta atau preferensi ke memory Hermes")
@app_commands.describe(content="Fakta/preferensi yang ingin disimpan", scope="Scope memory", project_key="Nama project, wajib untuk scope project")
@app_commands.choices(scope=[
    app_commands.Choice(name="User pribadi", value="user"),
    app_commands.Choice(name="Channel ini", value="channel"),
    app_commands.Choice(name="Project", value="project"),
])
async def remember_command(interaction: discord.Interaction, content: str, scope: app_commands.Choice[str], project_key: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    try:
        key = project_key.strip() or memory_store.infer_project(content)
        record = await asyncio.to_thread(
            memory_store.remember,
            owner_id=interaction.user.id,
            channel_id=interaction.channel_id,
            content=content,
            scope=scope.value,
            project_key=key,
        )
        audit("memory_explicit_remember", memory_id=record["id"], scope=record["scope"], redactions=record["redaction_count"], requester_id=interaction.user.id)
        redaction_note = f" Token/password yang terdeteksi disamarkan: **{record['redaction_count']}**." if record["redaction_count"] else ""
        await interaction.response.send_message(
            f"🧠 Memory tersimpan: `{record['id']}` · scope `{record['scope']}`.{redaction_note}",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Memory belum tersimpan: `{type(exc).__name__}` — {str(exc)[:500]}", ephemeral=True)


@tree.command(name="memory", description="Lihat memory dan ringkasan percakapan Hermes")
@app_commands.describe(query="Kata kunci opsional", project_key="Filter project opsional")
async def memory_command(interaction: discord.Interaction, query: str = "", project_key: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    try:
        key = project_key.strip() or memory_store.infer_project(query)
        items = await asyncio.to_thread(
            memory_store.search,
            owner_id=interaction.user.id,
            channel_id=interaction.channel_id,
            query=query,
            project_key=key,
        )
        old_summaries = await asyncio.to_thread(
            memory_store.summaries,
            owner_id=interaction.user.id,
            channel_id=interaction.channel_id,
            project_key=key,
        )
        audit("memory_read", result_count=len(items), summary_count=len(old_summaries), requester_id=interaction.user.id)
        await interaction.response.send_message(memory_store.render(items, old_summaries), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⚠️ Memory belum bisa dibaca: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="forget", description="Hapus memory milikmu berdasarkan ID")
@app_commands.describe(memory_id="ID MEM-... dari /memory")
async def forget_command(interaction: discord.Interaction, memory_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    try:
        record = await asyncio.to_thread(memory_store.forget, memory_id.strip(), interaction.user.id)
        audit("memory_forgotten", memory_id=record["id"], requester_id=interaction.user.id)
        await interaction.response.send_message(f"🗑️ Memory `{record['id']}` sudah dihapus dari hasil pencarian Hermes.", ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Memory belum dihapus: `{type(exc).__name__}` — {str(exc)[:500]}", ephemeral=True)


@tree.command(name="command", description="Kirim perintah bahasa natural read-only")
@app_commands.describe(text="Contoh: cek Core Gateway atau cek ERP")
async def command(interaction: discord.Interaction, text: str) -> None:
    await send_command_result(interaction, text)


@tree.command(name="status", description="Cek status ERP dan MikroTik secara read-only")
async def status(interaction: discord.Interaction) -> None:
    await send_command_result(interaction, "status")


@tree.command(name="hermes-info", description="Lihat versi, stack, provider, model, dan instalasi Hermes")
async def hermes_info_command(interaction: discord.Interaction) -> None:
    await send_command_result(interaction, "info Hermes")


@tree.command(name="status-hermes", description="Cek kondisi build dan runtime Hermes")
async def status_hermes(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    if not is_mikrotik_owner(interaction.user.id):
        await interaction.response.send_message("🔒 Status build yang memuat kondisi MikroTik hanya bisa dibuka Henka.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        snapshot_path = Path(os.getenv("MONITOR_STATE_FILE", "./data/baseline.json"))
        snapshot = await asyncio.to_thread(monitor.load_json, snapshot_path)
        diagnostics = await asyncio.to_thread(local_runner.diagnose, "hermes")
        task_state = str(diagnostics.get("task_scheduler", "UNKNOWN")) if isinstance(diagnostics, dict) else "UNKNOWN"
        latency = bot.latency * 1000 if bot.is_ready() else None
        response = hermes_build_status(snapshot, task_state, bot.is_ready(), latency)
        await interaction.followup.send(response[:1900], ephemeral=True)
    except Exception as exc:
        audit("hermes_build_status_failed", error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send("⚠️ Status build Hermes belum bisa dibaca. Bot tetap berjalan; coba lagi sebentar.", ephemeral=True)


@tree.command(name="devops-status", description="Cek kesiapan 15 domain DevOps dan adapter lokal read-only")
async def devops_status_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(devops_adapters.diagnose)
        audit("devops_status", requester_id=interaction.user.id)
        await interaction.followup.send(devops_adapters.render_status(result), ephemeral=True)
    except Exception as exc:
        audit("devops_status_failed", error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send(f"⚠️ Pemeriksaan DevOps gagal: `{type(exc).__name__}`. Detail disimpan di audit lokal.", ephemeral=True)


@tree.command(name="external-status", description="Cek adapter GitHub, Vercel, dan Neon secara read-only")
async def external_status_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(external_adapters.diagnose)
        audit("external_status", requester_id=interaction.user.id)
        await interaction.followup.send(external_adapters.render(result), ephemeral=True)
    except Exception as exc:
        audit("external_status_failed", error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send(f"⚠️ Pemeriksaan external adapter gagal: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="external-propose", description="Ajukan perubahan Git/Vercel/Neon melalui approval gate")
@app_commands.describe(service="github, vercel, neon, atau git", action="Aksi allowlist", payload="JSON payload sesuai action")
@app_commands.choices(service=[
    app_commands.Choice(name="GitHub", value="github"),
    app_commands.Choice(name="Vercel", value="vercel"),
    app_commands.Choice(name="Neon", value="neon"),
    app_commands.Choice(name="Git lokal", value="git"),
], action=[
    app_commands.Choice(name="GitHub issue", value="issue_create"),
    app_commands.Choice(name="Komentar PR", value="pr_comment"),
    app_commands.Choice(name="Buat PR", value="pr_create"),
    app_commands.Choice(name="Vercel preview", value="preview_deploy"),
    app_commands.Choice(name="Vercel production", value="production_deploy"),
    app_commands.Choice(name="Neon branch", value="branch_create"),
    app_commands.Choice(name="Git branch", value="branch_create"),
    app_commands.Choice(name="Git commit", value="commit"),
    app_commands.Choice(name="Git push branch", value="push_branch"),
])
async def external_propose_command(interaction: discord.Interaction, service: app_commands.Choice[str], action: app_commands.Choice[str], payload: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction); return
    try:
        body = parse_json_body(payload)
        record = await asyncio.to_thread(external_changes.create, service.value, action.value, body, interaction.user.id)
        audit("external_change_created", change_id=record["id"], service=service.value, action=action.value, requester_id=interaction.user.id)
        await interaction.response.send_message(external_changes.describe(record), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ External change ditolak: `{type(exc).__name__}` — {str(exc)[:500]}", ephemeral=True)


@tree.command(name="external-approve", description="Approve dan jalankan external write yang sudah diperiksa")
@app_commands.describe(change_id="ID EXT-... dari external-propose")
async def external_approve_command(interaction: discord.Interaction, change_id: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction); return
    await interaction.response.defer(ephemeral=True)
    try:
        record = await asyncio.to_thread(external_changes.approve_and_execute, change_id, interaction.user.id)
        audit("external_change_executed", change_id=change_id.upper(), service=record.get("service"), action=record.get("action"), requester_id=interaction.user.id)
        await interaction.followup.send(external_changes.describe(record), ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"⛔ External write gagal: `{type(exc).__name__}`. Detail aman disimpan di audit lokal.", ephemeral=True)


@tree.command(name="external-deny", description="Batalkan external write yang menunggu approval")
@app_commands.describe(change_id="ID EXT-... dari external-propose")
async def external_deny_command(interaction: discord.Interaction, change_id: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction); return
    try:
        record = await asyncio.to_thread(external_changes.deny, change_id, interaction.user.id)
        audit("external_change_denied", change_id=change_id.upper(), requester_id=interaction.user.id)
        await interaction.response.send_message(external_changes.describe(record), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ External deny gagal: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="devops-plan", description="Buat rencana aman untuk workflow DevOps Hermes")
@app_commands.describe(stage="Tahap workflow", request="Scope pekerjaan yang ingin direncanakan")
@app_commands.choices(stage=[
    app_commands.Choice(name="tool adapter DevOps", value="adapters"),
    app_commands.Choice(name="incident diagnosis", value="incident"),
    app_commands.Choice(name="programmer workflow", value="programmer"),
    app_commands.Choice(name="DevOps workflow", value="devops"),
])
async def devops_plan_command(interaction: discord.Interaction, stage: app_commands.Choice[str], request: str = "") -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    try:
        result = devops_adapters.workflow_plan(stage.value, request)
        audit("devops_plan", stage=stage.value, requester_id=interaction.user.id)
        await interaction.response.send_message(devops_adapters.render_plan(result), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⚠️ Rencana DevOps gagal dibuat: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="check", description="Cek target tertentu secara read-only")
@app_commands.describe(target="erp, core, lan, atau all")
@app_commands.choices(target=[
    app_commands.Choice(name="all", value="all"),
    app_commands.Choice(name="erp", value="erp"),
    app_commands.Choice(name="core", value="core"),
    app_commands.Choice(name="lan", value="lan"),
])
async def check(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    await send_command_result(interaction, f"cek {target.value}")


@tree.command(name="diagnose", description="Diagnostik Hermes, Discord, jaringan, dan laptop secara read-only")
async def diagnose_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    result = await asyncio.to_thread(self_heal.diagnose, bot)
    audit("self_diagnose", requester_id=interaction.user.id)
    await interaction.followup.send(format_self_heal(result), ephemeral=True)


@tree.command(name="self-heal", description="Diagnosis atau restart Hermes melalui jalur aman")
@app_commands.describe(mode="diagnose = read-only, repair = restart Hermes allowlisted")
@app_commands.choices(mode=[
    app_commands.Choice(name="diagnose", value="diagnose"),
    app_commands.Choice(name="repair", value="repair"),
])
async def self_heal_command(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    if mode.value == "repair" and not can_request_local_action(interaction.user.id):
        await interaction.response.send_message("🔒 Repair Hermes hanya untuk operator aksi laptop.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(self_heal.diagnose, bot) if mode.value == "diagnose" else await asyncio.to_thread(self_heal.repair)
        audit("self_heal_command", mode=mode.value, requester_id=interaction.user.id)
        await interaction.followup.send(format_self_heal(result), ephemeral=True)
    except Exception as exc:
        audit("self_heal_failed", mode=mode.value, error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send(f"⛔ Self-heal gagal: `{type(exc).__name__}`. Detail disimpan di audit lokal.", ephemeral=True)


@tree.command(name="terminal", description="Jalankan preset PowerShell diagnostik yang aman")
@app_commands.describe(preset="Preset read-only; arbitrary PowerShell tidak diizinkan")
@app_commands.choices(preset=[app_commands.Choice(name=name, value=name) for name in sorted(self_heal.TERMINAL_PRESETS)])
async def terminal_command(interaction: discord.Interaction, preset: app_commands.Choice[str]) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(self_heal.terminal_preset, preset.value)
        audit("terminal_preset_command", preset=preset.value, requester_id=interaction.user.id)
        await interaction.followup.send(format_terminal_preset(result), ephemeral=True)
    except Exception as exc:
        audit("terminal_preset_failed", preset=preset.value, error=type(exc).__name__, requester_id=interaction.user.id)
        await interaction.followup.send(f"⛔ Preset terminal gagal: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="job", description="Buat rencana pekerjaan Hermes dengan risiko dan approval")
@app_commands.describe(prompt="Contoh: buat laporan ERP atau cek jaringan dan buat laporan")
async def job_command(interaction: discord.Interaction, prompt: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    record = await asyncio.to_thread(job_executor.create, prompt, interaction.user.id)
    audit("job_created", job_id=record["id"], kind=record["kind"], requester_id=interaction.user.id)
    await interaction.response.send_message(format_job(record), ephemeral=True)


@tree.command(name="job-approve", description="Setujui job yang membutuhkan approval")
@app_commands.describe(job_id="ID job, contoh JOB-1A2B3C4D")
async def job_approve_command(interaction: discord.Interaction, job_id: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    try:
        record = await asyncio.to_thread(job_executor.approve, job_id, interaction.user.id)
        await interaction.response.send_message(format_job(record), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Approval job gagal: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="job-run", description="Jalankan job yang sudah direncanakan atau disetujui")
@app_commands.describe(job_id="ID job yang ingin dijalankan")
async def job_run_command(interaction: discord.Interaction, job_id: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        record = await asyncio.to_thread(job_executor.run, job_id, interaction.user.id)
        audit("job_run", job_id=job_id.upper(), status=record.get("status"), requester_id=interaction.user.id)
        await interaction.followup.send(format_job(record), ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"⛔ Job belum dijalankan: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="job-status", description="Lihat status dan progress job Hermes")
@app_commands.describe(job_id="ID job")
async def job_status_command(interaction: discord.Interaction, job_id: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    try:
        record = await asyncio.to_thread(job_executor.get, job_id, interaction.user.id)
        await interaction.response.send_message(format_job(record), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Status job tidak bisa dibaca: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="job-list", description="Lihat daftar job Hermes milikmu")
async def job_list_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    records = await asyncio.to_thread(job_executor.list_jobs, interaction.user.id)
    if not records:
        await interaction.response.send_message("📦 Belum ada job Hermes.", ephemeral=True)
        return
    lines = ["📦 **Job Hermes terbaru**"]
    for record in records:
        lines.append(f"- `{record['id']}` · `{record['kind']}` · **{record['status']}**")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@tree.command(name="job-schedule", description="Jadwalkan job harian Hermes pada waktu WIB")
@app_commands.describe(prompt="Pekerjaan harian", time_local="Waktu WIB format HH:MM, contoh 07:30")
async def job_schedule_command(interaction: discord.Interaction, prompt: str, time_local: str) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    try:
        record = await asyncio.to_thread(job_executor.schedule, prompt, interaction.user.id, time_local)
        await interaction.response.send_message(f"⏰ **Jadwal job dibuat**\nID: `{record['id']}`\nWaktu: **{record['time_local']} WIB setiap hari**\nPekerjaan: {record['prompt']}", ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Jadwal tidak dibuat: `{type(exc).__name__}`.", ephemeral=True)


@tree.command(name="job-schedules", description="Lihat job harian Hermes yang aktif")
async def job_schedules_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction) or not is_mikrotik_owner(interaction.user.id):
        await reject(interaction)
        return
    records = await asyncio.to_thread(job_executor.list_schedules, interaction.user.id)
    if not records:
        await interaction.response.send_message("⏰ Belum ada jadwal job aktif.", ephemeral=True)
        return
    lines = ["⏰ **Jadwal job aktif**"]
    lines.extend(f"- `{x['id']}` · **{x['time_local']} WIB** · {x['prompt']}" for x in records)
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


@tree.command(name="diff", description="Bandingkan snapshot terbaru dengan baseline")
async def diff(interaction: discord.Interaction) -> None:
    await send_command_result(interaction, "diff")


@tree.command(name="propose", description="Siapkan perubahan ERP untuk approval eksplisit")
@app_commands.describe(method="POST atau PATCH", endpoint="Contoh /api/erp/ingredients atau /api/erp/ingredients/12", body="JSON object tanpa rahasia")
@app_commands.choices(method=[app_commands.Choice(name="POST", value="POST"), app_commands.Choice(name="PATCH", value="PATCH")])
async def propose(interaction: discord.Interaction, method: app_commands.Choice[str], endpoint: str, body: str) -> None:
    await propose_action(interaction, method.value, endpoint, body)


@tree.command(name="approve", description="Konfirmasi dan jalankan approval yang dibuat user ini")
@app_commands.describe(approval_id="Contoh HMR-1A2B3C4D")
async def approve(interaction: discord.Interaction, approval_id: str) -> None:
    await approve_action(interaction, approval_id)


@tree.command(name="deny", description="Batalkan approval yang dibuat user ini")
@app_commands.describe(approval_id="ID approval yang dibatalkan")
async def deny(interaction: discord.Interaction, approval_id: str) -> None:
    await deny_action(interaction, approval_id)


@tree.command(name="laptop-check", description="Diagnostik laptop read-only")
@app_commands.describe(target="system, disk, processes, network, wireguard, atau hermes")
@app_commands.choices(target=[app_commands.Choice(name=x, value=x) for x in sorted(local_runner.ALLOWED)])
async def laptop_check(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    if not is_mikrotik_owner(interaction.user.id):
        await interaction.response.send_message("🔒 Diagnostik laptop hanya bisa diakses Henka.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    result = await asyncio.to_thread(local_runner.diagnose, target.value)
    audit("local_readonly", target=target.value)
    await interaction.followup.send(format_local_diagnostic(target.value, result), ephemeral=True)


@tree.command(name="laptop-propose", description="Ajukan aksi laptop yang memerlukan approval")
@app_commands.describe(action="Nyalakan/restart ChatGPT, restart Hermes, atau jadwalkan restart laptop 20 detik")
@app_commands.choices(action=[
    app_commands.Choice(name="start_chatgpt", value="start_chatgpt"),
    app_commands.Choice(name="restart_chatgpt", value="restart_chatgpt"),
    app_commands.Choice(name="restart_hermes", value="restart_hermes"),
    app_commands.Choice(name="restart_laptop", value="restart_laptop"),
])
async def laptop_propose(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    if not can_request_local_action(interaction.user.id):
        await interaction.response.send_message("🔒 Aksi sistem laptop hanya bisa diminta operator laptop.", ephemeral=True); return
    try:
        record = local_gate.propose(interaction.user.id, action.value)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Aksi ditolak: {exc}", ephemeral=True); return
    audit("local_approval_created", approval_id=record["id"], action=action.value, requester_id=interaction.user.id)
    await interaction.response.send_message(f"**Local approval `{record['id']}`**\nAksi: `{action.value}`\nBerlaku 10 menit. Konfirmasi dengan `/approved approval_id:{record['id']}`.")


async def approve_local_action(interaction: discord.Interaction, approval_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        record, result = await asyncio.to_thread(local_gate.approve, approval_id, interaction.user.id)
        audit("local_approval_executed", approval_id=record["id"], action=record["action"], approver_id=interaction.user.id)
        await interaction.followup.send(f"✅ Aksi `{record['action']}` dijalankan.\n{format_local_action_result(record['action'], result)}", ephemeral=True)
    except Exception as exc:
        audit("local_approval_failed", approval_id=approval_id, error=type(exc).__name__, approver_id=interaction.user.id)
        record = local_gate._read().get(approval_id.upper(), {})
        if record.get("status") == "UNKNOWN":
            await interaction.followup.send("⚠️ Hasil aksi lokal belum bisa dipastikan. Approval dikunci agar tidak dijalankan ulang; periksa status laptop/Hermes sebelum membuat permintaan baru.", ephemeral=True)
        else:
            await interaction.followup.send(f"⛔ Aksi tidak dijalankan: {exc}", ephemeral=True)


@tree.command(name="laptop-approve", description="Konfirmasi aksi laptop yang sudah dipreview")
@app_commands.describe(approval_id="ID LAP-...")
async def laptop_approve(interaction: discord.Interaction, approval_id: str) -> None:
    await approve_local_action(interaction, approval_id)


@tree.command(name="approved", description="Approve aksi laptop Hermes")
@app_commands.describe(approval_id="ID LAP-...")
async def approved(interaction: discord.Interaction, approval_id: str) -> None:
    await approve_local_action(interaction, approval_id)


@tree.command(name="eng-task", description="Buat task engineering untuk Codex/ChatGPT")
@app_commands.describe(title="Judul masalah atau pekerjaan", description="Detail pekerjaan", evidence="Bukti/log ringkas")
async def eng_task(interaction: discord.Interaction, title: str, description: str, evidence: str = "") -> None:
    if not permitted(interaction):
        await reject(interaction); return
    record = engineering_tasks.create(title, description, evidence)
    audit("engineering_task_created", task_id=record["id"], requester_id=interaction.user.id)
    await interaction.response.send_message(f"✅ Task engineering `{record['id']}` dibuat. Codex/ChatGPT bisa membaca file task lokalnya.\n`data/engineering/tasks/{record['id']}.md`")


@tree.command(name="eng-list", description="Lihat task engineering yang masih terbuka")
async def eng_list(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    items = engineering_tasks.list_open()
    text = "**Engineering tasks terbuka**\n" + ("\n".join(f"- `{x['id']}` — {x['title']}" for x in items) if items else "Belum ada task terbuka.")
    await interaction.response.send_message(text)


@tree.command(name="audit", description="Ringkasan history dan anomaly Hermes")
async def audit_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    if not is_mikrotik_owner(interaction.user.id):
        await interaction.response.send_message("🔒 Audit mencakup riwayat MikroTik dan hanya bisa dibuka Henka.", ephemeral=True); return
    await interaction.response.send_message(audit_summary.render(os.getenv("HERMES_HISTORY_DB", "./data/history.db")))


@tree.command(name="provider-status", description="Cek validitas akses Groq dan Google AI Studio tanpa menampilkan key")
async def provider_status_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    await interaction.response.defer(ephemeral=True)
    statuses = await asyncio.to_thread(llm_chat.provider_status)
    audit("provider_status", result=statuses)
    await interaction.followup.send("🔌 **Status provider AI**\n" + "\n".join(f"- `{name}`: {'✅' if status == 'VALID' else '⚠️'} **{status}**" for name, status in statuses.items()) + "\n\n🔒 Nilai token tidak pernah ditampilkan.", ephemeral=True)


@tree.command(name="saham", description="Ringkasan data harian saham dan indikator dasar")
@app_commands.describe(symbol="Ticker, misalnya BBCA atau BBCA.JK untuk IDX")
async def saham(interaction: discord.Interaction, symbol: str) -> None:
    if not permitted(interaction):
        await reject(interaction)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(stock_market.analyze, symbol)
        audit("stock_lookup", symbol=result["symbol"], exchange=result["exchange"], requester_id=interaction.user.id)
        await interaction.followup.send(stock_market.render(result), ephemeral=True)
    except Exception as exc:
        audit("stock_lookup_error", symbol=symbol[:30], error=type(exc).__name__, requester_id=interaction.user.id)
        if not os.getenv("API_CO_ID_KEY", "").strip():
            response = "📈 Data saham belum aktif. Buat API key gratis Api.co.id, lalu simpan sebagai `API_CO_ID_KEY` di `.env` lokal—jangan kirim key ke Discord."
        else:
            response = "⚠️ Data historis saham belum tersedia. Cek ticker atau ketersediaan histori pada sumber data, lalu coba lagi."
        await interaction.followup.send(response, ephemeral=True)


@tree.command(name="audit-dashboard", description="Buat dashboard audit HTML lokal")
async def audit_dashboard_command(interaction: discord.Interaction) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    if not is_mikrotik_owner(interaction.user.id):
        await interaction.response.send_message("🔒 Dashboard audit dapat memuat riwayat MikroTik dan hanya bisa dibuat Henka.", ephemeral=True); return
    try:
        path = audit_dashboard.generate(os.getenv("HERMES_HISTORY_DB", "./data/history.db"), os.getenv("HERMES_AUDIT_DASHBOARD", "./data/audit-dashboard.html"))
        await interaction.response.send_message(f"✅ Dashboard audit dibuat lokal: `{path}`")
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Dashboard belum bisa dibuat: {exc}", ephemeral=True)


@tree.command(name="change-propose", description="Ajukan approval merge/deploy")
@app_commands.describe(target="staging atau production", action="merge atau deploy", reference="branch, commit, atau PR", summary="Ringkasan perubahan")
@app_commands.choices(target=[app_commands.Choice(name="staging", value="staging"), app_commands.Choice(name="production", value="production")], action=[app_commands.Choice(name="merge", value="merge"), app_commands.Choice(name="deploy", value="deploy")])
async def change_propose(interaction: discord.Interaction, target: app_commands.Choice[str], action: app_commands.Choice[str], reference: str, summary: str) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    try:
        record = change_gate.propose(interaction.user.id, target.value, action.value, reference, summary)
    except ValueError as exc:
        await interaction.response.send_message(f"⛔ Proposal ditolak: {exc}", ephemeral=True); return
    audit("change_approval_created", approval_id=record["id"], target=target.value, action=action.value, requester_id=interaction.user.id)
    await interaction.response.send_message(f"**Change approval `{record['id']}`**\nTarget: `{target.value}`\nAksi: `{action.value}`\nReference: `{reference}`\nRingkasan: {summary}\n\nKonfirmasi dengan `/change-approve {record['id']}`. Hermes hanya mencatat approval; eksekusi merge/deploy tetap dilakukan setelah review Codex/ChatGPT.")


@tree.command(name="change-approve", description="Setujui proposal merge/deploy setelah review")
@app_commands.describe(approval_id="ID CHG-...")
async def change_approve(interaction: discord.Interaction, approval_id: str) -> None:
    if not permitted(interaction):
        await reject(interaction); return
    try:
        record = change_gate.approve(approval_id, interaction.user.id)
    except Exception as exc:
        await interaction.response.send_message(f"⛔ Tidak bisa approve: {exc}", ephemeral=True); return
    audit("change_approval_approved", approval_id=record["id"], target=record["target"], action=record["action"], approver_id=interaction.user.id)
    await interaction.response.send_message(f"✅ `{record['id']}` approved. Jalankan merge/deploy hanya setelah diff, checks, dan rollback diverifikasi oleh Codex/ChatGPT.")


if __name__ == "__main__":
    # Windows Task Scheduler can leave a child Python process alive after the
    # wrapper is stopped. Refuse a second gateway process instead of creating
    # duplicate Discord replies.
    instance_lock = Path(os.getenv("HERMES_DISCORD_INSTANCE_LOCK", "./data/discord-bot-instance.lock"))
    try:
        with exclusive(instance_lock):
            bot.run(TOKEN)
    except OSError as exc:
        print(f"Discord bot instance already active; exiting ({type(exc).__name__})")
