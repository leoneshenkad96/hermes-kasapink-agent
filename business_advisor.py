"""Read-only Kasapink business analysis helpers."""

from __future__ import annotations

import re
import os
import urllib.parse
from datetime import date, timedelta
from typing import Any

import monitor


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float) -> str:
    return f"Rp{value:,.0f}".replace(",", ".")


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _parse_price(text: str) -> float | None:
    match = re.search(r"(?:rp|idr)\s*([0-9][0-9.,]*)", text, re.I)
    if match:
        raw = match.group(1).replace(".", "").replace(",", "")
        return float(raw) if raw.isdigit() else None
    match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\s*(?:ribu|rb)\b", text, re.I)
    if match:
        return float(match.group(1).replace(",", ".")) * 1000
    return None


def parse_price_question(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").strip().split())
    lowered = value.lower()
    price = _parse_price(value)
    if price is None or not any(term in lowered for term in ("untung", "margin", "hpp", "harga jual", "profit")):
        return None
    match = re.search(
        r"(?:jual|harga(?: jual)?|produk|menu)\s+(.+?)(?=\s+(?:rp|idr)\s*[0-9]|\s+[0-9]+(?:[.,][0-9]+)?\s*(?:ribu|rb)\b|\s+(?:masih\s+)?(?:untung|margin|profit|hpp)\b|\?|$)",
        value,
        re.I,
    )
    product = match.group(1).strip(" .,?!") if match else ""
    if not product:
        return None
    product = re.sub(r"^(?:nasi|produk|menu)\s+", lambda m: m.group(0), product, flags=re.I)
    return {"product_query": product[:120], "proposed_price": price}


def _session() -> tuple[str, Any, dict[str, str]]:
    base_url = os.getenv("KASAPINK_BASE_URL", "").strip().rstrip("/")
    username = os.getenv("KASAPINK_USERNAME", "").strip()
    password = os.getenv("KASAPINK_PASSWORD", "")
    if not base_url or not username or not password:
        raise RuntimeError("Kasapink read-only credentials are not configured")
    opener, headers = monitor.login_kasapink(base_url, username, password)
    return base_url, opener, headers


def _get(base_url: str, opener: Any, headers: dict[str, str], path: str, params: dict[str, str] | None = None) -> Any:
    url = f"{base_url}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return monitor.request_json(url, headers, opener=opener)


def _payload_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key, payload.get("items", []))
        return value if isinstance(value, list) else []
    return payload if isinstance(payload, list) else []


def _state() -> tuple[dict[str, Any], str]:
    base_url, opener, headers = _session()
    payload = _get(base_url, opener, headers, "/api/erp/state")
    if not isinstance(payload, dict):
        raise RuntimeError("ERP state response is invalid")
    return payload, "GET /api/erp/state"


def _find_product(products: list[dict[str, Any]], query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    needle = _normalize(query)
    exact = [item for item in products if _normalize(str(item.get("name", ""))) == needle]
    if len(exact) == 1:
        return exact[0], exact
    partial = [item for item in products if needle and needle in _normalize(str(item.get("name", "")))]
    if len(partial) == 1:
        return partial[0], partial
    return None, partial or exact


def analyze_price(text: str) -> dict[str, Any]:
    parsed = parse_price_question(text)
    if not parsed:
        return {"status": "NOT_UNDERSTOOD", "reason": "Sebutkan nama produk dan harga, misalnya: jual nasi bakar Rp15.000 masih untung nggak?"}
    state, source = _state()
    products = _payload_list(state.get("products"), "products")
    product, candidates = _find_product(products, parsed["product_query"])
    if product is None:
        if candidates:
            return {"status": "AMBIGUOUS", "query": parsed["product_query"], "candidates": [item.get("name", "?") for item in candidates[:8]], "source": source}
        return {"status": "NOT_FOUND", "query": parsed["product_query"], "source": source}
    hpp = _number(product.get("averageCost"))
    proposed = float(parsed["proposed_price"])
    result: dict[str, Any] = {
        "status": "OK",
        "product_name": product.get("name", parsed["product_query"]),
        "business_type": product.get("businessType", "unknown"),
        "selling_price_in_erp": _number(product.get("sellingPrice")),
        "proposed_price": proposed,
        "hpp": hpp,
        "needs_recipe": product.get("needsRecipe"),
        "source": source,
        "generated_at": state.get("generatedAt"),
        "data_status": state.get("dataStatus"),
    }
    if hpp is None or hpp <= 0:
        result["status"] = "NO_HPP"
        return result
    gross_profit = proposed - hpp
    result["gross_profit"] = gross_profit
    result["gross_margin_pct"] = (gross_profit / proposed * 100) if proposed else None
    result["markup_pct"] = (gross_profit / hpp * 100) if hpp else None
    return result


def render_price(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "NOT_UNDERSTOOD":
        return "🧮 " + result["reason"]
    if status == "NOT_FOUND":
        return f"⚠️ Produk **{result.get('query', '?')}** belum ditemukan di ERP. Gue belum akan menebak HPP atau margin."
    if status == "AMBIGUOUS":
        options = ", ".join(f"`{item}`" for item in result.get("candidates", []))
        return f"⚠️ Nama produk masih ambigu. Kandidat di ERP: {options}. Sebutkan nama yang lebih spesifik."
    lines = [
        f"🧮 **Analisis harga — {result['product_name']}**",
        f"- Harga yang diuji: **{_money(result['proposed_price'])}**",
    ]
    if result.get("selling_price_in_erp") is not None:
        lines.append(f"- Harga jual di ERP: **{_money(result['selling_price_in_erp'])}**")
    if status == "NO_HPP":
        lines.extend([
            "- HPP ERP: **belum tersedia/valid**",
            "- Kesimpulan: **belum bisa memastikan untung atau rugi**",
            "Pastikan resep dan biaya bahan/preparation sudah lengkap. Hermes tidak akan mengarang angka.",
        ])
    else:
        icon = "✅" if result["gross_profit"] > 0 else ("⚠️" if result["gross_profit"] == 0 else "🚨")
        lines.extend([
            f"- HPP bahan menurut ERP: **{_money(result['hpp'])}** per unit",
            f"- Estimasi laba kotor: **{_money(result['gross_profit'])}** per unit",
            f"- Gross margin: **{result['gross_margin_pct']:.2f}%**",
            f"- Kesimpulan: {icon} **{'masih ada margin kotor' if result['gross_profit'] > 0 else ('impas' if result['gross_profit'] == 0 else 'rugi kotor')}**",
        ])
        if result.get("business_type") == "Makanan":
            lines.append("Catatan: ini laba kotor berbasis HPP bahan/preparation, bukan laba bersih setelah listrik, tenaga kerja, sewa, dan biaya operasional lain.")
    lines.append(f"Sumber: `{result.get('source', 'ERP')}` · generated `{result.get('generated_at') or 'tidak tersedia'}` · read-only, tidak ada transaksi yang diubah.")
    return "\n".join(lines)[:1900]


def report(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=6)
    if start > end or end > date.today():
        raise ValueError("periode tidak valid; gunakan tanggal WIB yang tidak melebihi hari ini")
    base_url, opener, headers = _session()
    params = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    finance = _get(base_url, opener, headers, "/api/erp/finance", params)
    fnb = _get(base_url, opener, headers, "/api/erp/fnb-report", params)
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "finance": finance, "fnb": fnb, "source": "GET /api/erp/finance + GET /api/erp/fnb-report"}


def render_report(result: dict[str, Any]) -> str:
    finance = result.get("finance") if isinstance(result.get("finance"), dict) else {}
    fnb = result.get("fnb") if isinstance(result.get("fnb"), dict) else {}
    lines = [f"📊 **Ringkasan bisnis Kasapink** · `{result['start_date']}` s/d `{result['end_date']}`"]
    for label, key in (("Revenue", "revenue"), ("HPP/COGS", "costOfGoodsSold"), ("Pembelian", "purchases"), ("Laba kotor", "grossProfit")):
        value = _number(finance.get(key))
        lines.append(f"- {label}: **{_money(value) if value is not None else 'data tidak tersedia'}**")
    if fnb:
        for label, key in (("Food cost", "recordedFoodCostPercentage"), ("Waste", "wasteCost"), ("Laba bersih tercatat", "netProfit")):
            value = _number(fnb.get(key))
            suffix = "%" if key == "recordedFoodCostPercentage" and value is not None else ""
            lines.append(f"- {label}: **{f'{value:.2f}{suffix}' if value is not None else 'data tidak tersedia'}**")
    lines.append("Data diambil read-only dari ERP; revenue, laba kotor, laba bersih, dan cash flow tidak disamakan.")
    lines.append(f"Sumber: `{result['source']}`")
    return "\n".join(lines)[:1900]
