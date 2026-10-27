import unittest
from unittest.mock import patch

import discord_bot
import intent_router
import product_research


class IntentRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_virtual_office_is_not_marketplace_search(self):
        text = "Rekomendasi virtual office untuk usaha kecil"
        self.assertTrue(intent_router.is_virtual_office_request(text))
        self.assertIsNone(intent_router.route_product_search(text))
        self.assertFalse(product_research.looks_like_product_search(text))
        self.assertEqual(discord_bot.classify_command(text)[0], "service_recommendation")

    def test_explicit_marketplace_request_still_routes_to_search(self):
        text = "Carikan marketplace untuk sewa virtual office"
        self.assertEqual(intent_router.route_product_search(text).name, "product_search")
        self.assertEqual(discord_bot.classify_command(text)[0], "product_search")

    def test_normal_product_request_is_unchanged(self):
        self.assertEqual(discord_bot.classify_command("Carikan tablet buat gambar")[0], "product_search")

    def test_content_drafts_do_not_hit_mutation_block(self):
        self.assertEqual(discord_bot.classify_command("Buat SOP operasional virtual office")[0], "content_draft")
        self.assertEqual(discord_bot.classify_command("Buatkan layout kantor virtual pixel-art")[0], "content_draft")

    def test_investment_action_is_checked_before_stock_lookup(self):
        self.assertEqual(discord_bot.classify_command("Beli saham ASII")[0], "investment_action")
        self.assertEqual(discord_bot.classify_command("Jual saham BBCA")[0], "investment_action")
        self.assertEqual(discord_bot.classify_command("Cek harga saham ASII")[0], "stock_lookup")

    def test_virtual_office_provider_prices_are_marked_as_unverified(self):
        self.assertEqual(
            discord_bot.classify_command("Rekomendasi provider virtual office untuk usaha kecil")[0],
            "service_recommendation",
        )

    async def test_investment_action_does_not_call_stock_worker(self):
        with patch.object(discord_bot.stock_market, "analyze") as analyze, patch.object(discord_bot, "audit"):
            reply = await discord_bot.dispatch_text("Beli saham ASII", 246656697949618179)
        self.assertIn("tidak bisa mengeksekusi", reply)
        analyze.assert_not_called()

    async def test_service_recommendation_has_unverified_data_guard(self):
        with patch.object(discord_bot, "audit"):
            reply = await discord_bot.dispatch_text("Rekomendasi provider virtual office untuk usaha kecil", 246656697949618179)
        self.assertIn("belum bisa memverifikasi", reply)
        self.assertIn("jangan anggap", reply)


if __name__ == "__main__":
    unittest.main()
