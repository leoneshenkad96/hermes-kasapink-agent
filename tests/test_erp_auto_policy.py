import unittest

import command_templates
import erp_auto_policy
import form_parser


TODAY = "2026-10-23"
CATALOG = {
    "ingredients": [{"id": 1, "name": "Garam"}, {"id": 2, "name": "Tepung Terigu"}],
    "products": [{"id": 9, "name": "Nasi Bakar", "sellingPrice": 15000, "businessType": "Makanan"}],
    "preparations": [],
}


class ErpAutoPolicyTests(unittest.TestCase):
    def ingredient(self, *, name="Gula Aren", stock="2", cost="10000", include_cost=True):
        text = command_templates.match("tambah stok makanan") + "\n" + "\n".join([
            f"Nama bahan: {name}", "Kategori: Bahan Pokok", "Satuan: kg", f"Stok awal: {stock}",
            *( [f"Biaya per satuan stok awal (Rp): {cost}"] if include_cost else [] ),
            "Batas minimum: 1",
        ])
        method, path, body = form_parser.parse(text)
        return erp_auto_policy.evaluate(method, path, body, form_parser.extract_fields(text), CATALOG,
                                        enabled=True, today=TODAY)

    def test_complete_new_low_value_ingredient_is_automatic(self):
        allowed, reason = self.ingredient()
        self.assertTrue(allowed)
        self.assertIn("nama unik", reason)

    def test_incomplete_high_value_or_duplicate_ingredient_needs_approval(self):
        self.assertFalse(self.ingredient(include_cost=False)[0])
        self.assertFalse(self.ingredient(stock="20", cost="10000")[0])
        self.assertFalse(self.ingredient(name="Garam")[0])

    def test_purchase_auto_requires_today_valid_catalog_and_low_total(self):
        text = "Catat belanja\nTanggal: 2026-10-23\nAsal belanja: Pasar\nDaftar item: Garam — 1 — 50000"
        parsed = form_parser.parse(text, CATALOG)
        self.assertTrue(erp_auto_policy.evaluate(*parsed, form_parser.extract_fields(text), CATALOG,
                                                 enabled=True, today=TODAY)[0])
        old_text = text.replace(TODAY, "2026-10-22")
        old_parsed = form_parser.parse(old_text, CATALOG)
        self.assertFalse(erp_auto_policy.evaluate(*old_parsed, form_parser.extract_fields(old_text), CATALOG,
                                                  enabled=True, today=TODAY)[0])

    def test_sale_auto_uses_current_catalog_price_and_caps_total(self):
        text = "Catat penjualan\nTanggal: 2026-10-23\nKategori: Makanan\nDaftar item: Nasi Bakar — 2"
        parsed = form_parser.parse(text, CATALOG)
        self.assertTrue(erp_auto_policy.evaluate(*parsed, form_parser.extract_fields(text), CATALOG,
                                                 enabled=True, today=TODAY)[0])
        expensive = {**CATALOG, "products": [{"id": 9, "name": "Nasi Bakar", "sellingPrice": 60000, "businessType": "Makanan"}]}
        self.assertFalse(erp_auto_policy.evaluate(*parsed, form_parser.extract_fields(text), expensive,
                                                  enabled=True, today=TODAY)[0])

    def test_high_impact_recipes_and_disabled_policy_require_approval(self):
        body = {"items": [{"ingredientId": 1, "qtyRequired": 1, "recipeUnit": "kg"}]}
        self.assertFalse(erp_auto_policy.evaluate("PUT", "/api/erp/products/9/recipe", body,
                                                  {"namaatauidproduk": "Nasi Bakar", "daftarbahan": "Garam"},
                                                  CATALOG, enabled=True, today=TODAY)[0])
        self.assertFalse(self.ingredient_eval_enabled(False))

    def ingredient_eval_enabled(self, enabled):
        text = command_templates.match("tambah stok makanan") + "\n" + "\n".join([
            "Nama bahan: Gula Aren", "Kategori: Bahan Pokok", "Satuan: kg", "Stok awal: 2",
            "Biaya per satuan stok awal (Rp): 10000", "Batas minimum: 1",
        ])
        method, path, body = form_parser.parse(text)
        return erp_auto_policy.evaluate(method, path, body, form_parser.extract_fields(text), CATALOG,
                                        enabled=enabled, today=TODAY)[0]


if __name__ == "__main__":
    unittest.main()
