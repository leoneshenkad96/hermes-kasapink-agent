import unittest

import command_templates
import form_parser


CATALOG = {
    "ingredients": [{"id": 4, "name": "Tepung Terigu"}, {"id": 5, "name": "Telur"}],
    "products": [{"id": 9, "name": "Nasi Bakar"}],
    "preparations": [{"id": 12, "name": "Sambal"}],
}


class TemplateParserTests(unittest.TestCase):
    def test_ingredient_template_choice_labels_parse(self):
        text = command_templates.match("tambah stok makanan") + "\n" + "\n".join([
            "Nama bahan: Tepung Terigu", "Kategori (Bahan Pokok, lainnya): Bahan Pokok",
            "Satuan (kg, gram): kg", "Stok awal: 10", "Biaya per satuan stok awal (Rp): 14000",
            "Batas minimum: 3",
        ])
        method, path, body = form_parser.parse(text)
        self.assertEqual((method, path), ("POST", "/api/erp/ingredients"))
        self.assertEqual((body["category"], body["unit"], body["stock"]), ("Bahan Pokok", "kg", 10))
        self.assertEqual(body["openingUnitCost"], 14000)

    def test_processed_product_is_recipe_product(self):
        text = command_templates.match("tambah produk makanan") + "\n" + "\n".join([
            "Nama produk: Nasi Bakar", "Jenis (produk jadi / produk olahan): produk olahan",
            "Harga jual per unit (Rp): 15000", "Stok awal (isi 0 jika olahan): 0",
            "Biaya beli per unit (Rp): 0",
        ])
        _, _, body = form_parser.parse(text)
        self.assertTrue(body["needsRecipe"])
        self.assertEqual(body["stock"], 0)

    def test_name_resolution_for_purchase_sales_recipe_and_stock_count(self):
        purchase = "Catat belanja\nTanggal (YYYY-MM-DD): 2026-10-23\nAsal belanja (Pasar/Toko): Pasar\nDaftar item (nama atau ID bahan — jumlah — total biaya Rp): Tepung Terigu — 5 — 70000"
        _, path, body = form_parser.parse(purchase, CATALOG)
        self.assertEqual(path, "/api/erp/purchases")
        self.assertEqual(body["items"][0]["ingredientId"], 4)

        sale = "Catat penjualan\nTanggal (YYYY-MM-DD): 2026-10-23\nKategori (Makanan/Parfum): Makanan\nDaftar item (nama atau ID produk — jumlah terjual): Nasi Bakar — 3"
        _, path, body = form_parser.parse(sale, CATALOG)
        self.assertEqual(path, "/api/erp/sales")
        self.assertEqual(body["items"][0], {"productId": 9, "quantity": 3})

        recipe = "Atur resep produk\nNama atau ID produk: Nasi Bakar\nDaftar bahan (nama/ID — jumlah — satuan): Tepung Terigu — 250 — gram"
        _, path, body = form_parser.parse(recipe, CATALOG)
        self.assertEqual(path, "/api/erp/products/9/recipe")
        self.assertEqual(body["items"][0]["ingredientId"], 4)

        count = "Stok opname\nTanggal (YYYY-MM-DD): 2026-10-23\nDaftar item (nama atau ID bahan/preparation — stok fisik aktual): Tepung Terigu — 8"
        _, path, body = form_parser.parse(count, CATALOG)
        self.assertEqual(path, "/api/erp/stock-counts")
        self.assertEqual(body["items"][0], {"ingredientId": 4, "countedStock": 8})

    def test_legacy_item_field_with_embedded_guidance(self):
        old_form = "Catat penjualan\nTanggal (YYYY-MM-DD): 2026-10-23\nKategori (Makanan/Parfum): Makanan\nDaftar item: nama atau ID produk — jumlah terjual: Nasi Bakar — 3"
        _, path, body = form_parser.parse(old_form, CATALOG)
        self.assertEqual(path, "/api/erp/sales")
        self.assertEqual(body["items"][0]["productId"], 9)

    def test_ambiguous_and_missing_catalog_entries_fail_closed(self):
        duplicate = {"ingredients": [{"id": 1, "name": "Garam"}, {"id": 2, "name": "Garam"}]}
        with self.assertRaisesRegex(ValueError, "ambigu"):
            form_parser._resolve("Garam", duplicate["ingredients"], "bahan")
        with self.assertRaisesRegex(ValueError, "tidak ditemukan"):
            form_parser._resolve("Tidak Ada", CATALOG["ingredients"], "bahan")

    def test_incomplete_form_never_becomes_action(self):
        text = command_templates.match("tambah stok makanan")
        self.assertIsNone(form_parser.parse(text))

    def test_fractional_sales_quantity_is_rejected(self):
        sale = "Catat penjualan\nTanggal: 2026-10-23\nKategori: Makanan\nDaftar item: Nasi Bakar — 1,5"
        with self.assertRaisesRegex(ValueError, "bilangan bulat"):
            form_parser.parse(sale, CATALOG)


if __name__ == "__main__":
    unittest.main()
