"""Render a concise, read-only daily ERP and MikroTik digest."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from datetime import timedelta, timezone

import stock_market


TZ = timezone(timedelta(hours=7), name="WIB")


def _money(value: Any) -> str:
    try:
        return f"Rp {float(value):,.0f}"
    except (TypeError, ValueError):
        return "tidak tersedia"


def _resource(router: dict[str, Any]) -> dict[str, Any]:
    snapshot = router.get("snapshot", {}) if isinstance(router, dict) else {}
    rows = snapshot.get("resource", []) if isinstance(snapshot, dict) else []
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}


def render(snapshot: dict[str, Any], recent_anomalies: list[dict[str, Any]] | None = None) -> str:
    systems = snapshot.get("systems", {})
    captured = snapshot.get("captured_at", "waktu tidak tersedia")
    try:
        local_time = datetime.fromisoformat(captured.replace("Z", "+00:00")).astimezone(TZ)
        captured = local_time.strftime("%d %b %Y %H:%M WIB")
    except (TypeError, ValueError):
        pass
    lines = [f"🌅 **Rekap Harian Hermes Bossun — {datetime.now(TZ):%d %b %Y}**", f"Snapshot: `{captured}`"]

    if recent_anomalies:
        lines.append("\n🔎 **Anomali 24 jam terakhir**")
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for anomaly in sorted(recent_anomalies, key=lambda item: severity_order.get(item.get("severity", "LOW"), 9))[:5]:
            lines.append(f"- **{anomaly.get('severity', 'INFO')}** `{anomaly.get('system', 'unknown')}` — {anomaly.get('message', 'Perlu ditinjau')} ")
        if len(recent_anomalies) > 5:
            lines.append(f"- …dan {len(recent_anomalies) - 5} temuan lain (lihat `/audit`).")
    else:
        lines.append("\n🔎 **Anomali 24 jam terakhir:** tidak ada temuan rule-based yang tercatat di history lokal.")

    erp = systems.get("kasapink", {})
    lines.append("\n🧾 **ERP Henka**")
    if erp.get("status") != "OK":
        lines.append(f"- Status: 🚨 **{erp.get('status', 'UNKNOWN')}**")
    else:
        state = erp.get("state", {})
        today = state.get("today", {}) if isinstance(state, dict) else {}
        ingredients = state.get("ingredients", []) if isinstance(state, dict) else []
        products = state.get("products", []) if isinstance(state, dict) else []
        preps = state.get("preparations", []) if isinstance(state, dict) else []
        low = [row for row in ingredients + preps if isinstance(row, dict) and _as_float(row.get("stock")) <= _as_float(row.get("minStock"))]
        lines.extend([
            "- Status: ✅ **Terhubung**",
            f"- Item bahan / produk / preparation: **{len(ingredients)} / {len(products)} / {len(preps)}**",
            f"- Item pada/bawah stok minimum: **{len(low)}**",
        ])
        if low:
            lines.append("- Perlu dicek: " + "; ".join(
                f"{row.get('name', 'item')} ({row.get('stock', '?')} {row.get('unit', '')}, min {row.get('minStock', '?')})"
                for row in sorted(low, key=lambda x: _as_float(x.get("stock")) - _as_float(x.get("minStock")))[:5]
            ))
        if isinstance(today, dict):
            lines.append(f"- Rekap ERP `{today.get('date', 'tanggal tidak tersedia')}`: omzet **{_money(today.get('revenue'))}**, HPP **{_money(today.get('costOfGoodsSold'))}**, laba kotor **{_money(today.get('grossProfit'))}**, pembelian **{_money(today.get('purchases'))}**")

    lines.append("\n🛜 **MikroTik**")
    for name, label in (("core_gateway", "Core Gateway"), ("lan_controller", "LAN Controller")):
        router = systems.get(name, {})
        if router.get("status") != "OK":
            lines.append(f"- {label}: 🚨 **{router.get('status', 'UNKNOWN')}**")
            continue
        resource = _resource(router)
        cpu = resource.get("cpu-load", "?")
        memory = ""
        try:
            free = int(resource.get("free-memory", 0)) / (1024 ** 2)
            total = int(resource.get("total-memory", 0)) / (1024 ** 2)
            memory = f" · RAM sisa {free:.0f}/{total:.0f} MB"
        except (TypeError, ValueError):
            pass
        interfaces = router.get("snapshot", {}).get("interfaces", [])
        active_down = [row for row in interfaces if isinstance(row, dict) and str(row.get("disabled", "false")).lower() not in {"true", "yes"} and str(row.get("running", "false")).lower() not in {"true", "yes"}]
        lines.append(f"- {label}: ✅ terhubung · CPU **{cpu}%**{memory} · interface enabled tidak running: **{len(active_down)}**")

    lines.append("\nℹ️ Ringkasan bersifat read-only; stok/belanja/penjualan tidak diubah.")
    return "\n".join(lines)


def render_stock(
    stock_results: list[tuple[str, dict[str, Any] | None]],
    session: str = "close",
    ihsg: dict[str, Any] | None = None,
) -> str:
    session_label = "Sesi I — snapshot sementara setelah sesi pagi" if session == "morning" else "Penutupan hari — data setelah sesi sore"
    lines = [f"📈 **Rekap Saham Hermes — {session_label} · {datetime.now(TZ):%d %b %Y}**"]
    lines.append("\n🇮🇩 **Tren IHSG**")
    if ihsg:
        last = float(ihsg["last"])
        change = ihsg.get("change")
        change_pct = ihsg.get("change_pct")
        if change is None and ihsg.get("previous_close") is not None:
            change = last - float(ihsg["previous_close"])
        if change_pct is None and ihsg.get("previous_close"):
            change_pct = (last / float(ihsg["previous_close"]) - 1) * 100
        if change is None or change_pct is None:
            trend = "perubahan terhadap penutupan sebelumnya belum tersedia"
        else:
            direction = "menguat" if float(change) > 0 else "melemah" if float(change) < 0 else "tidak berubah"
            trend = f"{direction} **{abs(float(change)):,.2f} poin ({abs(float(change_pct)):.2f}%)** dari penutupan sebelumnya"
        lines.extend([
            f"- Level terakhir: **{last:,.2f}** · tanggal `{ihsg.get('date') or 'tidak tersedia'}`",
            f"- Perubahan hari ini: {trend}",
            f"- Status pasar: **{ihsg.get('market_status') or 'tidak tersedia'}** · snapshot `{ihsg.get('as_of') or 'waktu tidak tersedia'}`",
        ])
    else:
        lines.append("- Data IHSG sementara tidak tersedia; detail saham tetap ditampilkan bila ada.")
    lines.append("\n🎲 **Sampel saham**")
    if not stock_results:
        lines.append("Watchlist belum diisi. Atur `HERMES_STOCK_WATCHLIST` di `.env` lokal.")
    else:
        for symbol, result in stock_results:
            if result is None:
                lines.append(f"- `{symbol}`: data belum tersedia (cek API key atau kuota provider).")
                continue
            prior = result.get("previous_close")
            change = f" · perubahan sesi terakhir {result['change_pct']:+.2f}%" if prior is not None else ""
            lines.append(f"- **{symbol}** · penutupan `{result['date']}`: **Rp {result['close']:,.2f}**{change}")
    detail = "Harga sesi pagi masih snapshot sementara dan dapat berubah pada sesi II." if session == "morning" else "Data setelah penutupan sesi II; bukan harga real-time."
    lines.append(f"\n🎲 Saham dipilih acak dari instrumen IHSG, bukan watchlist tetap atau ranking pasar. {detail} Top gainers/losers pasar penuh memerlukan endpoint berbayar. Bukan rekomendasi transaksi.")
    return "\n".join(lines)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def configured_watchlist() -> list[str]:
    raw = os.getenv("HERMES_STOCK_WATCHLIST", "")
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()][:5]


def fetch_watchlist() -> list[tuple[str, dict[str, Any] | None]]:
    results = []
    for symbol in configured_watchlist():
        try:
            results.append((symbol, stock_market.analyze(symbol)))
        except Exception:
            results.append((symbol, None))
    return results


def fetch_stock_sample() -> list[tuple[str, dict[str, Any] | None]]:
    configured = configured_watchlist()
    if configured:
        symbols = configured
    else:
        try:
            count = int(os.getenv("HERMES_STOCK_RANDOM_COUNT", "5"))
            symbols = stock_market.random_symbols(count)
        except Exception:
            return [("sampel acak IHSG", None)]
    results = []
    for symbol in symbols:
        try:
            results.append((symbol, stock_market.analyze(symbol)))
        except Exception:
            results.append((symbol, None))
    return results


def fetch_ihsg() -> dict[str, Any] | None:
    try:
        return stock_market.analyze_ihsg()
    except Exception:
        return None
