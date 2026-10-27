"""Conservative rules for automatic, low-impact ERP entries."""

from __future__ import annotations

import os
from datetime import date
from difflib import SequenceMatcher
from typing import Any


FOOD_CATEGORIES = {
    "bahan pokok", "bumbu & rempah", "protein", "sayuran", "buah",
    "dairy & olahan susu", "bahan minuman", "bahan pelengkap", "kemasan", "lainnya",
}
PERFUME_CATEGORIES = {
    "bibit / fragrance oil", "alcohol & solvent", "fixative & additive",
    "pewarna", "kemasan parfum", "aksesoris", "lainnya",
}


def _cap() -> float:
    try:
        value = float(os.getenv("HERMES_ERP_AUTO_MAX_VALUE", "100000"))
        return value if value >= 0 else 0
    except ValueError:
        return 0


def _today() -> str:
    # The Windows host and Hermes schedule use Jakarta time.
    from datetime import datetime, timedelta, timezone
    jakarta = timezone(timedelta(hours=7))
    return datetime.now(jakarta).date().isoformat()


def _catalog_names(catalog: dict[str, Any], key: str) -> list[str]:
    rows = catalog.get(key, [])
    return [str(row.get("name", "")).strip() for row in rows if isinstance(row, dict) and row.get("name")]


def _name_is_new(name: str, existing: list[str]) -> tuple[bool, str]:
    normalized = " ".join(name.casefold().split())
    if any(" ".join(item.casefold().split()) == normalized for item in existing):
        return False, "nama bahan sudah ada; perlu dicek manual agar tidak membuat duplikat"
    close = [item for item in existing if SequenceMatcher(None, normalized, " ".join(item.casefold().split())).ratio() >= 0.88]
    if close:
        return False, "nama bahan mirip dengan item ERP yang sudah ada; perlu konfirmasi manual"
    return True, "nama baru unik di katalog ERP terbaru"


def evaluate(
    method: str,
    path: str,
    body: dict[str, Any],
    fields: dict[str, str],
    catalog: dict[str, Any] | None,
    *,
    enabled: bool | None = None,
    today: str | None = None,
) -> tuple[bool, str]:
    """Return whether the exact ERP write is safe for no-approval execution."""
    if enabled is None:
        enabled = os.getenv("HERMES_ERP_AUTO_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return False, "eksekusi otomatis ERP sedang dinonaktifkan"
    if not catalog or not isinstance(catalog, dict):
        return False, "katalog ERP terbaru tidak tersedia untuk validasi"

    if method.upper() == "POST" and path == "/api/erp/ingredients":
        required = {"namabahan", "kategori", "satuan", "stokawal", "batasminimum"}
        cost_field = any(key.startswith("biayapersatuanstokawal") for key in fields)
        if not required.issubset(fields) or not cost_field:
            return False, "form bahan belum mengisi semua field wajib secara eksplisit"
        if not isinstance(catalog.get("ingredients"), list):
            return False, "daftar bahan ERP terbaru tidak tersedia untuk pemeriksaan duplikat"
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 100:
            return False, "nama bahan kosong atau terlalu panjang"
        stock_type = str(body.get("stockType", "")).casefold()
        categories = FOOD_CATEGORIES if stock_type == "makanan" else PERFUME_CATEGORIES if stock_type == "parfum" else set()
        if str(body.get("category", "")).casefold() not in categories:
            return False, "kategori bahan tidak cocok dengan pilihan yang dikenal"
        units = {"kg", "gram", "liter", "ml", "butir", "pcs", "ekor", "potong", "ikat", "pack", "box"} if stock_type == "makanan" else {"ml", "liter", "gram", "kg", "botol", "pcs", "pack"}
        if str(body.get("unit", "")).casefold() not in units:
            return False, "satuan bahan tidak cocok dengan pilihan yang dikenal"
        if not isinstance(body.get("stock"), (int, float)) or not isinstance(body.get("openingUnitCost"), (int, float)):
            return False, "stok awal atau biaya awal tidak valid"
        if body["stock"] * body["openingUnitCost"] > _cap():
            return False, f"nilai stok awal melewati batas otomatis Rp {_cap():,.0f}"
        unique, reason = _name_is_new(name, _catalog_names(catalog, "ingredients"))
        if not unique:
            return False, reason
        return True, f"field lengkap, nama unik, nilai stok awal di bawah Rp {_cap():,.0f}"

    if method.upper() == "POST" and path == "/api/erp/purchases":
        if not {"tanggal", "asalbelanja", "daftaritem"}.issubset(fields):
            return False, "data belanja belum lengkap"
        if fields.get("tanggal") != (today or _today()):
            return False, "transaksi bertanggal selain hari ini perlu review manual"
        if str(body.get("supplierType", "")).casefold() not in {"pasar", "toko", "grosir", "lainnya"}:
            return False, "asal belanja tidak cocok dengan pilihan yang dikenal"
        if not isinstance(catalog.get("ingredients"), list):
            return False, "daftar bahan ERP terbaru tidak tersedia untuk validasi belanja"
        items = body.get("items")
        if not isinstance(items, list) or not items:
            return False, "belum ada item belanja yang tervalidasi"
        if any(not isinstance(item, dict) or not isinstance(item.get("quantity"), (int, float)) or item["quantity"] <= 0 or not isinstance(item.get("totalCost"), (int, float)) or item["totalCost"] <= 0 for item in items):
            return False, "jumlah dan total biaya setiap item harus lebih dari nol"
        total = sum(item["totalCost"] for item in items)
        if total > _cap():
            return False, f"total belanja melewati batas otomatis Rp {_cap():,.0f}"
        return True, f"item cocok dengan katalog, transaksi hari ini, total di bawah Rp {_cap():,.0f}"

    if method.upper() == "POST" and path == "/api/erp/sales":
        if not {"tanggal", "kategori", "daftaritem"}.issubset(fields):
            return False, "data penjualan belum lengkap"
        if fields.get("tanggal") != (today or _today()):
            return False, "transaksi bertanggal selain hari ini perlu review manual"
        category = fields.get("kategori", "").casefold()
        if category not in {"makanan", "parfum"}:
            return False, "kategori penjualan tidak cocok dengan pilihan yang dikenal"
        items = body.get("items")
        products = catalog.get("products", [])
        if not isinstance(items, list) or not items or not isinstance(products, list):
            return False, "item penjualan belum cocok dengan katalog terbaru"
        total = 0.0
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("quantity"), int) or item["quantity"] <= 0:
                return False, "jumlah penjualan setiap item harus bilangan bulat lebih dari nol"
            product = next((row for row in products if isinstance(row, dict) and str(row.get("id")) == str(item.get("productId"))), None)
            if product is None:
                return False, "produk penjualan tidak ditemukan di katalog terbaru"
            product_type = product.get("businessType", product.get("stockType"))
            if not isinstance(product_type, str) or product_type.casefold() != category:
                return False, "kategori produk tidak bisa dicocokkan dengan kategori penjualan"
            raw_price = next((product[key] for key in ("sellingPrice", "selling_price", "price", "pricePerUnit") if key in product), None)
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                return False, "harga jual produk tidak tersedia untuk menghitung batas transaksi"
            if price < 0:
                return False, "harga jual produk tidak valid"
            total += price * item["quantity"]
        if total > _cap():
            return False, f"nilai penjualan melewati batas otomatis Rp {_cap():,.0f}"
        if total <= 0:
            return False, "nilai penjualan nol; perlu pengecekan harga sebelum dicatat"
        return True, f"produk dan harga cocok dengan katalog, transaksi hari ini, nilai di bawah Rp {_cap():,.0f}"

    return False, "jenis perubahan ini berdampak lebih tinggi dan tetap memerlukan approval"

