"""Natural-language form templates for the owner's ERP workflows."""

from __future__ import annotations

import re


def _section(title: str, fields: list[str], note: str) -> str:
    lines = [f"**{title}**", "Isi template ini; boleh lu copy lalu jawab langsung:", ""]
    lines.extend(f"`{field}`: _isi di sini_" for field in fields)
    lines.extend(["", note, "Setelah lengkap, gue rangkum dulu untuk approval. Belum ada data yang diubah."])
    return "\n".join(lines)


def is_latest_stock_query(text: str) -> bool:
    value = " ".join(text.lower().strip().split())
    return "opname" in value and not re.search(r"catat|simpan|input|rekam|hitung|adjust", value)


def match(text: str) -> str | None:
    value = " ".join(text.lower().strip().split())
    if re.search(r"tambah\s+(stok|bahan).*(makanan|parfum)?", value):
        business = "Makanan" if "parfum" not in value else "Parfum"
        categories = "Bahan Pokok, Bumbu & Rempah, Protein, Sayuran, Buah, Dairy & Olahan Susu, Bahan Minuman, Bahan Pelengkap, Kemasan, Lainnya" if business == "Makanan" else "Bibit / Fragrance Oil, Alcohol & Solvent, Fixative & Additive, Pewarna, Kemasan Parfum, Aksesoris, Lainnya"
        units = "kg, gram, liter, ml, butir, pcs, ekor, potong, ikat, pack, box" if business == "Makanan" else "ml, liter, gram, kg, botol, pcs, pack"
        return _section(f"Tambah stok bahan — {business}", ["Nama bahan", f"Kategori ({categories})", f"Satuan ({units})", "Stok awal", "Biaya per satuan stok awal (Rp)", "Batas minimum"], "Contoh: Tepung Terigu · Bahan Pokok · kg · 10 · 14000 · 3")
    if re.search(r"tambah\s+produk", value):
        business = "Parfum" if "parfum" in value else "Makanan"
        return _section(f"Tambah produk — {business}", ["Nama produk", "Jenis (produk jadi / produk olahan)", "Harga jual per unit (Rp)", "Stok awal (isi 0 jika olahan)", "Biaya beli per unit (Rp)", "Bahan utama otomatis (opsional, untuk produk olahan)"], "Untuk produk olahan, resep bisa diatur setelah produknya dibuat.")
    if re.search(r"atur\s+resep|ubah\s+resep|buat\s+resep", value):
        return _section("Atur resep produk", ["Nama atau ID produk", "Daftar bahan (nama/ID — jumlah — satuan)", "Catatan perubahan (opsional)"], "Contoh daftar: Tepung — 250 — gram; Telur — 2 — butir. Pastikan bahan sudah ada di ERP.")
    if re.search(r"cek\s+(produksi\s+)?prep|produksi\s+prep", value):
        return _section("Cek produksi preparation", ["Nama atau ID preparation (kosongkan untuk semua)", "Periode/tanggal (opsional)", "Yang ingin dicek (stok, resep, batch, atau semua)"], "Ini pemeriksaan read-only; tidak memerlukan approval untuk sekadar melihat data.")
    if re.search(r"catat\s+belanja|tambah\s+belanja", value):
        return _section("Catat belanja bahan", ["Tanggal (YYYY-MM-DD)", "Asal belanja (Pasar/Toko/Grosir/Lainnya)", "Daftar item (nama atau ID bahan — jumlah — total biaya Rp)"], "Contoh: 2026-10-22 · Pasar · Tepung — 5 kg — 70000; Telur — 30 butir — 60000")
    if re.search(r"catat\s+penjualan|tambah\s+penjualan", value):
        return _section("Catat penjualan", ["Tanggal (YYYY-MM-DD)", "Kategori (Makanan/Parfum)", "Daftar item (nama atau ID produk — jumlah terjual)"], "Contoh: 2026-10-22 · Makanan · Nasi Bakar — 3; Es Teh — 2")
    if re.search(r"catat\s+stok\s+opname|simpan\s+stok\s+opname|input\s+stok\s+opname|rekam\s+stok\s+opname|hitung\s+stok", value):
        return _section("Stok opname", ["Tanggal (YYYY-MM-DD)", "Daftar item (nama atau ID bahan/preparation — stok fisik aktual)"], "Opname akan membuat adjustment stok, jadi hasil akhirnya wajib direview dan di-approve.")
    return None
