"""Deterministic intent routing primitives for Hermes."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Intent:
    name: str
    value: str
    confidence: str = "high"


_SHOPPING_ACTIONS = (
    "cari harga", "cek harga", "carikan", "cariin", "rekomendasi", "bandingkan",
    "bandingin", "perbandingan", "compare", "cari produk", "cari barang",
    "di e-commerce", "di ecommerce", "di marketplace",
)
_SHOPPING_OBJECTS = (
    "barang", "produk", "tablet", "laptop", "hp", "ponsel", "handphone",
    "smartphone", "tv", "televisi", "monitor", "headphone", "earphone",
    "kamera", "printer", "smartwatch", "jam tangan", "kulkas", "mesin cuci",
    "vacuum", "sepatu", "tas", "parfum", "kursi", "meja", "ssd", "ram",
    "router", "perangkat", "marketplace", "e-commerce", "ecommerce",
)
_VIRTUAL_OFFICE_TERMS = (
    "virtual office", "virtual-office", "kantor virtual", "alamat bisnis",
    "domisili usaha", "sewa alamat", "ruang kantor", "coworking",
)
_EXPLICIT_SHOPPING_TERMS = (
    "belanja", "beli", "listing", "toko online", "marketplace", "e-commerce", "ecommerce",
)
_CONTENT_DRAFT_TERMS = (
    "sop", "layout", "onboarding", "materi", "konten", "copywriting",
    "dokumentasi", "naskah", "desain", "draft", "rencana", "workflow",
)
_CONTENT_DRAFT_ACTIONS = ("buat", "buatkan", "bikinin", "susun", "tulis", "rancang", "bantu")
_INVESTMENT_ACTIONS = ("beli", "jual", "buy", "sell", "borong", "entry", "exit", "masuk posisi")
_SERVICE_TERMS = ("provider", "penyedia", "harga", "biaya", "pilihan", "paket", "rekomendasi")
_QUESTION_PREFIXES = (
    "apakah", "apa ", "kenapa", "mengapa", "gimana", "bagaimana", "cara ",
    "bisa ", "boleh ", "jelaskan", "jelasin", "mau tanya", "ingin tahu",
    "bedanya ", "perbedaan ",
)


def normalize(text: str) -> str:
    return " ".join(str(text).lower().strip().split())


def looks_like_question(text: str) -> bool:
    value = normalize(text)
    return value.endswith("?") or any(value.startswith(prefix) or f" {prefix}" in value for prefix in _QUESTION_PREFIXES)


def is_virtual_office_request(text: str) -> bool:
    value = normalize(text)
    return any(term in value for term in _VIRTUAL_OFFICE_TERMS)


def route_product_search(text: str) -> Intent | None:
    value = normalize(text)
    if not any(term in value for term in _SHOPPING_ACTIONS):
        return None
    # A recommendation about a virtual office is a business/service question,
    # not an e-commerce lookup. Explicit marketplace language may override it.
    if is_virtual_office_request(value) and not any(term in value for term in _EXPLICIT_SHOPPING_TERMS):
        return None
    if not any(term in value for term in _SHOPPING_OBJECTS):
        return None
    return Intent("product_search", text, "high")


def looks_like_virtual_office_service_recommendation(text: str) -> bool:
    value = normalize(text)
    return (
        is_virtual_office_request(value)
        and any(term in value for term in _SERVICE_TERMS)
        and not any(term in value for term in _EXPLICIT_SHOPPING_TERMS)
    )


def looks_like_content_draft(text: str) -> bool:
    value = normalize(text)
    return any(term in value for term in _CONTENT_DRAFT_ACTIONS) and any(term in value for term in _CONTENT_DRAFT_TERMS)


def looks_like_investment_action(text: str) -> bool:
    value = normalize(text)
    if not any(term in value for term in _INVESTMENT_ACTIONS):
        return False
    if any(term in value for term in ("saham", "investasi", "reksadana", "crypto", "kripto")):
        return True
    return bool(re.search(r"\b[A-Z]{4}\b", str(text)))
