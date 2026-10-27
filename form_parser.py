"""Conservative parser for completed ERP templates (no writes or guessing)."""

from __future__ import annotations

import re
from typing import Any


def _key(label: str) -> str:
    # Choices in template labels are guidance, not part of the field name.
    label = re.sub(r"\s*\([^)]*\)", "", label).strip().lower()
    return re.sub(r"[^a-z0-9]", "", label)


def _values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = _key(key.strip().strip("`* "))
        value = value.strip().strip("_").strip()
        # Accept legacy templates whose guidance was embedded after a colon
        # in the field label (e.g. "Daftar item: nama/ID — jumlah: ...").
        if field in {"daftaritem", "daftarbahan"} and ": " in value:
            value = value.rsplit(": ", 1)[-1].strip()
        if value and value.lower() not in {"isi di sini", "-"}:
            result[field] = value
    return result


def extract_fields(text: str) -> dict[str, str]:
    """Return normalized, explicitly supplied form values for policy checks."""
    return _values(text)


def _number(value: str, key: str) -> float:
    cleaned = value.lower().replace("rp", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        number = float(cleaned)
        if number < 0:
            raise ValueError
        return number
    except ValueError as exc:
        raise ValueError(f"field {key} harus berupa angka nol atau lebih") from exc


def _get(values: dict[str, str], *prefixes: str) -> str | None:
    for key, value in values.items():
        if any(key.startswith(prefix) for prefix in prefixes):
            return value
    return None


def _parts(value: str) -> list[list[str]]:
    result = []
    for item in re.split(r"\s*;\s*|\n", value):
        item = item.strip(" -")
        if item:
            result.append([part.strip() for part in re.split(r"\s+[—–-]\s*", item)])
    return result


def _resolve(value: str, records: list[dict[str, Any]], kind: str) -> int:
    """Resolve an explicit ID or one unique exact name from a fresh catalog."""
    match = re.fullmatch(r"(?:id\s*)?#?([1-9][0-9]*)", value.strip(), re.I)
    if match:
        ident = int(match.group(1))
        if any(str(row.get("id")) == str(ident) for row in records):
            return ident
        raise ValueError(f"ID {kind} {ident} tidak ditemukan di ERP")
    if not records:
        raise ValueError(f"katalog {kind} tidak tersedia; coba lagi setelah status ERP berhasil dibaca")
    normalized = " ".join(value.casefold().split())
    found = [row for row in records if " ".join(str(row.get("name", "")).casefold().split()) == normalized]
    if len(found) == 1:
        return int(found[0]["id"])
    if len(found) > 1:
        raise ValueError(f"nama {kind} ambigu; gunakan ID")
    raise ValueError(f"nama {kind} '{value}' tidak ditemukan; periksa nama atau gunakan ID")


def _records(catalog: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    rows = (catalog or {}).get(key, [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def parse(text: str, catalog: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]] | None:
    values = _values(text)
    if "namabahan" in values and {"kategori", "satuan", "stokawal", "batasminimum"}.issubset(values):
        stock_type = "Parfum" if "parfum" in text.lower() else "Makanan"
        return "POST", "/api/erp/ingredients", {
            "name": values["namabahan"], "category": values["kategori"], "stockType": stock_type,
            "unit": values["satuan"], "stock": _number(values["stokawal"], "stok awal"),
            "openingUnitCost": _number(values.get("biayapersatuanstokawalrp", values.get("biayapersatuanstokawal", "0")), "biaya awal"),
            "minStock": _number(values["batasminimum"], "batas minimum"),
        }
    if "namaproduk" in values and ("hargajualperunitrp" in values or "hargajualperunit" in values):
        stock_type = "Parfum" if "parfum" in text.lower() else "Makanan"
        kind = values.get("jenis", "").lower()
        if not kind:
            raise ValueError("jenis produk wajib diisi: produk jadi atau produk olahan")
        needs_recipe = "olahan" in kind
        if not needs_recipe and "jadi" not in kind:
            raise ValueError("jenis produk harus 'produk jadi' atau 'produk olahan'")
        body: dict[str, Any] = {
            "name": values["namaproduk"], "sellingPrice": _number(values.get("hargajualperunitrp", values.get("hargajualperunit", "0")), "harga jual"),
            "businessType": stock_type, "needsRecipe": needs_recipe,
            "stock": 0 if needs_recipe else _number(values.get("stokawal", "0"), "stok awal"),
            "averageCost": 0 if needs_recipe else _number(values.get("biayabeliperunitrp", values.get("biayabeliperunit", "0")), "biaya beli"),
        }
        return "POST", "/api/erp/products", body

    date = _get(values, "tanggal")
    items = _get(values, "daftaritem")
    supplier = _get(values, "asalbelanja")
    if date and items and supplier:
        parsed_items = []
        for parts in _parts(items):
            if len(parts) < 3:
                raise ValueError("format belanja: nama/ID bahan — jumlah — total biaya")
            parsed_items.append({"ingredientId": _resolve(parts[0], _records(catalog, "ingredients"), "bahan"),
                                 "quantity": _number(parts[1], "jumlah"), "totalCost": _number(parts[2], "total biaya")})
        if not parsed_items:
            raise ValueError("minimal satu item belanja wajib diisi")
        return "POST", "/api/erp/purchases", {"date": date, "supplierType": supplier, "items": parsed_items}
    if date and items and _get(values, "kategori") and "penjualan" in text.lower():
        parsed_items = []
        for parts in _parts(items):
            if len(parts) < 2:
                raise ValueError("format penjualan: nama/ID produk — jumlah")
            quantity = _number(parts[1], "jumlah terjual")
            if not quantity.is_integer():
                raise ValueError("jumlah terjual harus bilangan bulat")
            parsed_items.append({"productId": _resolve(parts[0], _records(catalog, "products"), "produk"),
                                 "quantity": int(quantity)})
        if not parsed_items:
            raise ValueError("minimal satu item penjualan wajib diisi")
        return "POST", "/api/erp/sales", {"date": date, "items": parsed_items}
    if date and items and ("opname" in text.lower() or "fisik" in text.lower()):
        ingredients = _records(catalog, "ingredients")
        preparations = _records(catalog, "preparations")
        parsed_items = []
        for parts in _parts(items):
            if len(parts) < 2:
                raise ValueError("format opname: nama atau ingredient:ID/preparation:ID — stok fisik")
            target = parts[0].strip()
            match = re.fullmatch(r"(ingredient|preparation)\s*:?\s*(.+)", target, re.I)
            if match:
                kind, name = match.group(1).lower(), match.group(2)
                ident = _resolve(name, ingredients if kind == "ingredient" else preparations, kind)
            else:
                found_i = [r for r in ingredients if str(r.get("name", "")).casefold() == target.casefold()]
                found_p = [r for r in preparations if str(r.get("name", "")).casefold() == target.casefold()]
                if len(found_i) + len(found_p) != 1:
                    raise ValueError("nama item opname tidak ditemukan/ambigu; gunakan ingredient:ID atau preparation:ID")
                kind, ident = ("ingredient", int(found_i[0]["id"])) if found_i else ("preparation", int(found_p[0]["id"]))
            parsed_items.append({f"{kind}Id": ident, "countedStock": _number(parts[1], "stok fisik")})
        return "POST", "/api/erp/stock-counts", {"date": date, "items": parsed_items}

    product_id = _get(values, "namaatau idproduk", "namaatauidproduk")
    recipe_items = _get(values, "daftarbahan")
    if product_id and recipe_items:
        product_number = _resolve(product_id, _records(catalog, "products"), "produk")
        parsed_items = []
        for parts in _parts(recipe_items):
            if len(parts) < 3:
                raise ValueError("format resep: nama/ID bahan — jumlah — satuan")
            parsed_items.append({"ingredientId": _resolve(parts[0], _records(catalog, "ingredients"), "bahan"),
                                 "qtyRequired": _number(parts[1], "jumlah resep"), "recipeUnit": parts[2]})
        if not parsed_items:
            raise ValueError("minimal satu bahan resep wajib diisi")
        return "PUT", f"/api/erp/products/{product_number}/recipe", {"items": parsed_items}
    return None
