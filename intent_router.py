"""Deterministic intent routing primitives for Hermes."""

from __future__ import annotations

from dataclasses import dataclass


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


def normalize(text: str) -> str:
    return " ".join(str(text).lower().strip().split())


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
