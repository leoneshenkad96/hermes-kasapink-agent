import json
import os
import unittest
from unittest.mock import patch

import discord_bot
import product_research


class ProductSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_product_search_intent_and_non_product_chat(self):
        self.assertTrue(product_research.looks_like_product_search("Carikan tablet buat gambar, bandingkan harganya"))
        self.assertTrue(product_research.looks_like_product_search("Cek harga laptop di marketplace"))
        self.assertFalse(product_research.looks_like_product_search("Jelaskan sejarah tablet"))
        self.assertFalse(product_research.looks_like_product_search("Cek harga saham ASII"))
        self.assertEqual(discord_bot.classify_command("Carikan tablet buat gambar, bandingkan harganya")[0], "product_search")

    def test_search_filters_to_supported_marketplaces_and_keeps_links(self):
        payload = {"web": {"results": [
            {"title": "Tablet listing", "url": "https://www.tokopedia.com/store/tablet", "description": "Harga Rp3.000.000"},
            {"title": "Not marketplace", "url": "https://example.com/tablet", "description": "fake result"},
            {"title": "Unsafe scheme", "url": "http://shopee.co.id/product/1", "description": "not https"},
        ]}}
        response = unittest.mock.Mock()
        response.__enter__ = unittest.mock.Mock(return_value=response)
        response.__exit__ = unittest.mock.Mock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        with patch.object(product_research.urllib.request, "urlopen", return_value=response) as urlopen:
            found = product_research.search("tablet", api_key="not-a-real-key")
        self.assertEqual(len(found), 1)
        self.assertIn("tokopedia.com", found[0]["url"])
        request = urlopen.call_args.args[0]
        self.assertIn("country=ID", request.full_url)
        self.assertEqual(request.get_header("X-subscription-token"), "not-a-real-key")

    def test_missing_key_is_explicit_and_untrusted_sources_are_marked(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
            with self.assertRaises(product_research.SearchNotConfigured):
                product_research.search("tablet")
        context = product_research.model_context("tablet", [{"title": "Listing", "url": "https://shopee.co.id/item", "description": "Rp 1.000.000"}])
        self.assertIn("tidak tepercaya", context)
        self.assertIn("Jangan membeli, checkout", context)

    async def test_discord_reports_missing_search_key_without_searching(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}), \
             patch.object(product_research, "search") as search, \
             patch.object(discord_bot, "audit"):
            reply = await discord_bot.dispatch_text("Carikan tablet buat gambar", 246656697949618179)
        self.assertIn("BRAVE_SEARCH_API_KEY", reply)
        search.assert_not_called()

    async def test_discord_searches_then_grounds_model_answer_with_sources(self):
        results = [{"title": "Tablet listing", "url": "https://www.tokopedia.com/store/tablet", "description": "Harga indikatif Rp3.000.000"}]
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "configured"}), \
             patch.object(product_research, "search", return_value=results) as search, \
             patch.object(discord_bot.llm_chat, "chat", return_value=("Bandingkan listing ini.", "groq")) as chat, \
             patch.object(discord_bot, "audit"):
            reply = await discord_bot.dispatch_text("Carikan tablet buat gambar", 246656697949618179)
        self.assertIn("Bandingkan listing ini", reply)
        self.assertIn("Sumber listing", reply)
        self.assertIn("tokopedia.com", reply)
        search.assert_called_once()
        chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
