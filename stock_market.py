"""Read-only IDX daily historical market data using Api.co.id's free tier."""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any


API_ROOT = "https://use.api.co.id/idx/v2/chart/daily"
INSTRUMENTS_ROOT = "https://use.api.co.id/idx/v2/instruments"
QUOTE_ROOT = "https://use.api.co.id/idx/v2/chart/quote"
UNIVERSE_CACHE = Path(os.getenv("HERMES_STOCK_UNIVERSE_CACHE", "./data/stock-universe.json"))
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MONTHS = {
    "januari": 1, "january": 1, "jan": 1,
    "februari": 2, "february": 2, "feb": 2,
    "maret": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5, "may": 5,
    "juni": 6, "june": 6, "jun": 6,
    "juli": 7, "july": 7, "jul": 7,
    "agustus": 8, "august": 8, "agu": 8, "ags": 8, "aug": 8,
    "september": 9, "septemberl": 9, "sep": 9, "sept": 9,
    "oktober": 10, "october": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11,
    "desember": 12, "december": 12, "des": 12, "dec": 12,
}


def extract_dates(text: str) -> list[str]:
    """Extract ISO, Indonesian DD/MM/YYYY, and day-month-name-year dates."""
    found: list[tuple[int, str]] = []
    for match in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text):
        try:
            found.append((match.start(), date.fromisoformat(match.group()).isoformat()))
        except ValueError:
            continue
    for match in re.finditer(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", text):
        try:
            found.append((match.start(), date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()))
        except ValueError:
            continue
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    for match in re.finditer(rf"\b(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b", text, re.I):
        try:
            found.append((match.start(), date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1))).isoformat()))
        except ValueError:
            continue
    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def _validate_date(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not DATE_RE.fullmatch(value):
        raise ValueError(f"{label} harus berformat YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} bukan tanggal kalender yang valid") from exc
    return value


def _fetch_daily(symbol: str, start_date: str | None = None, end_date: str | None = None) -> tuple[list[dict[str, Any]], str]:
    api_key = os.getenv("API_CO_ID_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_CO_ID_KEY belum diisi di .env lokal")
    symbol = symbol.strip().upper().removesuffix(".JK")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,19}", symbol):
        raise ValueError("kode saham tidak valid")
    start_date = _validate_date(start_date, "Tanggal awal")
    end_date = _validate_date(end_date, "Tanggal akhir")
    if start_date and end_date and start_date > end_date:
        raise ValueError("tanggal awal harus sebelum atau sama dengan tanggal akhir")
    if start_date and end_date and (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days > 7305:
        raise ValueError("rentang maksimal 20 tahun")

    query: dict[str, str] = {"timeframe": "1d", "order": "asc"}
    if start_date:
        query["from"] = start_date
    else:
        query["from"] = (date.today() - timedelta(days=366)).isoformat()
    if end_date:
        query["to"] = end_date
    else:
        query["to"] = date.today().isoformat()

    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    # Defensive pagination bound; a year of daily bars should fit in one page.
    for _ in range(20):
        params = dict(query)
        if cursor:
            params["cursor"] = cursor
        request = urllib.request.Request(
            f"{API_ROOT}/{urllib.parse.quote(symbol)}?{urllib.parse.urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "HermesMonitor/1.0", "x-api-co-id": api_key},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("is_success") is not True:
            message = str(payload.get("message", "data saham tidak tersedia")) if isinstance(payload, dict) else "respons provider tidak valid"
            raise RuntimeError(message[:180])
        wrapper = payload.get("data")
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("data"), list):
            raise RuntimeError("format data harian dari provider tidak valid")
        rows.extend(row for row in wrapper["data"] if isinstance(row, dict))
        meta = wrapper.get("meta", {})
        cursor = meta.get("cursor") if isinstance(meta, dict) else None
        if not cursor:
            break
    else:
        raise RuntimeError("data terlalu banyak; perkecil rentang tanggal")

    rows = [row for row in rows if isinstance(row.get("date"), str) and row.get("close") is not None]
    if start_date:
        rows = [row for row in rows if row["date"] >= start_date]
    if end_date:
        rows = [row for row in rows if row["date"] <= end_date]
    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise RuntimeError("tidak ada data penutupan harian untuk ticker/rentang tersebut")
    try:
        if any(float(row["close"]) <= 0 for row in rows):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise RuntimeError("format harga dari provider tidak valid") from exc
    return rows, symbol


def analyze(symbol: str, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    rows, symbol = _fetch_daily(symbol, start_date, end_date)
    closes = [float(row["close"]) for row in rows]
    first, last = rows[0], rows[-1]
    return {
        "symbol": symbol,
        "currency": "IDR",
        "date": last["date"],
        "close": closes[-1],
        "previous_close": closes[-2] if len(closes) > 1 else None,
        "change_pct": (closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else 0.0,
        "average_close": sum(closes) / len(closes),
        "period_start": first["date"],
        "period_end": last["date"],
        "period_change_pct": (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else 0.0,
        "observations": len(closes),
        "source": "Api.co.id IDX Daily (harga penutupan historis)",
    }


def analyze_ihsg() -> dict[str, Any]:
    """Fetch the live IHSG index snapshot and its change from prior close."""
    api_key = os.getenv("API_CO_ID_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_CO_ID_KEY belum diisi di .env lokal")
    request = urllib.request.Request(
        f"{QUOTE_ROOT}/IHSG",
        headers={"Accept": "application/json", "User-Agent": "HermesMonitor/1.0", "x-api-co-id": api_key},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("is_success") is not True:
        message = str(payload.get("message", "snapshot IHSG tidak tersedia")) if isinstance(payload, dict) else "respons provider tidak valid"
        raise RuntimeError(message[:180])
    wrapper = payload.get("data")
    data = wrapper.get("data") if isinstance(wrapper, dict) else None
    if not isinstance(data, dict) or data.get("last") is None:
        raise RuntimeError("level IHSG belum tersedia dari provider")
    try:
        last = float(data["last"])
        previous_close = float(data["prev_close"]) if data.get("prev_close") is not None else None
        change = float(data["change"]) if data.get("change") is not None else None
        change_pct = float(data["change_pct"]) if data.get("change_pct") is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeError("format angka IHSG dari provider tidak valid") from exc
    if last <= 0:
        raise RuntimeError("level IHSG dari provider tidak valid")
    meta = wrapper.get("meta", {})
    return {
        "symbol": "IHSG",
        "name": str(data.get("name") or "Indeks Harga Saham Gabungan"),
        "date": data.get("date"),
        "last": last,
        "previous_close": previous_close,
        "change": change,
        "change_pct": change_pct,
        "market_status": data.get("market_status"),
        "as_of": meta.get("as_of") if isinstance(meta, dict) else None,
        "source": "Api.co.id IDX live quote",
    }


def random_symbols(count: int = 5, cache_days: int = 7) -> list[str]:
    """Pick a random sample from the cached whole-market IHSG instrument list."""
    api_key = os.getenv("API_CO_ID_KEY", "").strip()
    if not api_key:
        raise RuntimeError("API_CO_ID_KEY belum diisi di .env lokal")
    count = max(1, min(int(count), 10))
    symbols: list[str] = []
    try:
        cached = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
        age = time.time() - float(cached.get("fetched_at", 0))
        if age < cache_days * 86400:
            symbols = cached.get("symbols", [])
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    if not symbols:
        symbols = []
        cursor: str | None = None
        for _ in range(20):
            params = {"index": "IHSG", "limit": "100"}
            if cursor:
                params["cursor"] = cursor
            request = urllib.request.Request(
                f"{INSTRUMENTS_ROOT}?{urllib.parse.urlencode(params)}",
                headers={"Accept": "application/json", "User-Agent": "HermesMonitor/1.0", "x-api-co-id": api_key},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("is_success") is not True:
                message = str(payload.get("message", "daftar saham tidak tersedia")) if isinstance(payload, dict) else "respons provider tidak valid"
                raise RuntimeError(message[:180])
            wrapper = payload.get("data", {})
            rows = wrapper.get("data", []) if isinstance(wrapper, dict) else []
            if not isinstance(rows, list):
                raise RuntimeError("format daftar instrumen dari provider tidak valid")
            symbols.extend(
                row["symbol"].strip().upper()
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("symbol"), str)
                and re.fullmatch(r"[A-Z0-9]{4,5}", row["symbol"].strip().upper())
            )
            meta = wrapper.get("meta", {})
            cursor = meta.get("cursor") if isinstance(meta, dict) else None
            if not cursor:
                break
        else:
            raise RuntimeError("halaman daftar instrumen terlalu banyak; batasi scan untuk menjaga kuota")
        symbols = sorted(set(symbols))
        if not symbols:
            raise RuntimeError("daftar instrumen IHSG kosong")
        UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temp = UNIVERSE_CACHE.with_suffix(UNIVERSE_CACHE.suffix + ".tmp")
        temp.write_text(json.dumps({"fetched_at": time.time(), "symbols": symbols}), encoding="utf-8")
        temp.replace(UNIVERSE_CACHE)

    return random.sample(symbols, min(count, len(symbols)))


def render(result: dict[str, Any]) -> str:
    count = int(result["observations"])
    average = float(result["average_close"])
    close = float(result["close"])
    period_change = float(result["period_change_pct"])
    if count > 1:
        direction = "naik" if period_change > 0 else "turun" if period_change < 0 else "tidak berubah"
        period_text = f"Perubahan dari penutupan pertama ke terakhir: **{direction} {abs(period_change):.2f}%**."
    else:
        period_text = "Baru ada satu sesi dalam rentang ini; belum bisa menghitung perubahan periode."
    return (
        f"📈 **{result['symbol']} — data historis IDX**\n"
        f"- Periode data: `{result['period_start']}` sampai `{result['period_end']}` ({count} sesi bursa)\n"
        f"- Rata-rata harga penutupan: **Rp {average:,.2f}**\n"
        f"- Penutupan terakhir di rentang: **Rp {close:,.2f}** pada `{result['date']}`\n"
        f"- {period_text}\n\n"
        "ℹ️ Rata-rata dihitung dari harga penutupan tiap sesi yang tersedia. Jika tanggal yang diminta bukan hari bursa, data memakai sesi bursa yang tersedia di dalam rentang—bukan mengarang harga untuk tanggal itu. Data historis/EOD, bukan real-time. Ini informasi umum, bukan rekomendasi beli/jual.\n"
        f"Sumber: {result['source']}."
    )


def render_comparison(symbol: str, start: dict[str, Any], end: dict[str, Any]) -> str:
    start_close, end_close = float(start["close"]), float(end["close"])
    difference = end_close - start_close
    pct = difference / start_close * 100
    direction = "naik" if difference > 0 else "turun" if difference < 0 else "tidak berubah"
    return (
        f"📊 **{symbol} — perbandingan harga penutupan**\n"
        f"- `{start['date']}`: **Rp {start_close:,.2f}**\n"
        f"- `{end['date']}`: **Rp {end_close:,.2f}**\n"
        f"- Jadi harga {direction} **Rp {abs(difference):,.2f} ({abs(pct):.2f}%)**.\n\n"
        "Perbandingan memakai sesi bursa terdekat yang tersedia di dalam rentang tanggal, bukan harga intraday. Data historis ini bukan rekomendasi beli/jual."
    )
