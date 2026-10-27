import unittest

import discord_bot
import intent_router
import product_research


class IntentRouterTests(unittest.TestCase):
    def test_virtual_office_is_not_marketplace_search(self):
        text = "Rekomendasi virtual office untuk usaha kecil"
        self.assertTrue(intent_router.is_virtual_office_request(text))
        self.assertIsNone(intent_router.route_product_search(text))
        self.assertFalse(product_research.looks_like_product_search(text))
        self.assertEqual(discord_bot.classify_command(text)[0], "unknown")

    def test_explicit_marketplace_request_still_routes_to_search(self):
        text = "Carikan marketplace untuk sewa virtual office"
        self.assertEqual(intent_router.route_product_search(text).name, "product_search")
        self.assertEqual(discord_bot.classify_command(text)[0], "product_search")

    def test_normal_product_request_is_unchanged(self):
        self.assertEqual(discord_bot.classify_command("Carikan tablet buat gambar")[0], "product_search")


if __name__ == "__main__":
    unittest.main()
