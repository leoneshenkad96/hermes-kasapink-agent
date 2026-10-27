import os
import unittest
from unittest.mock import patch

import discord_bot


class ErpAutoRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_operator_cannot_trigger_auto_erp_write(self):
        text = "\n".join([
            "Tambah stok makanan", "Nama bahan: Gula Aren", "Kategori: Bahan Pokok",
            "Satuan: kg", "Stok awal: 2", "Biaya per satuan stok awal (Rp): 10000",
            "Batas minimum: 1",
        ])
        with patch.object(discord_bot.approval, "execute_automatically") as execute, patch.object(discord_bot, "audit"):
            response = await discord_bot.natural_reply(100, text, 999999999999999999, "unregistered")
        self.assertIn("belum mendapat hak operator ERP", response)
        execute.assert_not_called()

    async def test_complete_low_value_ingredient_uses_auto_executor(self):
        text = "\n".join([
            "Tambah stok makanan", "Nama bahan: Gula Aren", "Kategori: Bahan Pokok",
            "Satuan: kg", "Stok awal: 2", "Biaya per satuan stok awal (Rp): 10000",
            "Batas minimum: 1",
        ])
        catalog = {"ingredients": [], "products": [], "preparations": []}
        record = {"id": "HMR-AUTO0001", "path": "/api/erp/ingredients"}
        with patch.dict(os.environ, {"HERMES_ERP_AUTO_ENABLED": "true", "HERMES_ERP_AUTO_MAX_VALUE": "100000"}), \
             patch.object(discord_bot.monitor, "collect", return_value={"systems": {"kasapink": {"status": "OK", "state": catalog}}}), \
             patch.object(discord_bot.approval, "execute_automatically", return_value=(record, {})) as execute, \
             patch.object(discord_bot.approval, "create") as create_manual, \
             patch.object(discord_bot, "audit"):
            response = await discord_bot.natural_reply(100, text, 246656697949618179, "Henka")
        self.assertIn("Berhasil dicatat otomatis", response)
        execute.assert_called_once()
        create_manual.assert_not_called()

    async def test_high_impact_product_stays_in_manual_approval_path(self):
        text = "\n".join([
            "Tambah produk makanan", "Nama produk: Produk Baru",
            "Jenis (produk jadi / produk olahan): produk jadi", "Harga jual per unit (Rp): 25000",
            "Stok awal (isi 0 jika olahan): 0", "Biaya beli per unit (Rp): 0",
        ])
        catalog = {"ingredients": [], "products": [], "preparations": []}
        record = {"id": "HMR-MANUAL01", "status": "PENDING", "method": "POST", "expires_at": "later",
                  "path": "/api/erp/products", "body": {"name": "Produk Baru"}}
        with patch.object(discord_bot.approval, "create", return_value=record) as create_manual, \
             patch.object(discord_bot.approval, "execute_automatically") as execute, \
             patch.object(discord_bot, "audit"), \
             patch.object(discord_bot.monitor, "collect", return_value={"systems": {"kasapink": {"status": "OK", "state": catalog}}}):
            response = await discord_bot.natural_reply(100, text, 246656697949618179, "Henka")
        self.assertIn("Perlu approval manual", response)
        execute.assert_not_called()
        create_manual.assert_called_once()


if __name__ == "__main__":
    unittest.main()
