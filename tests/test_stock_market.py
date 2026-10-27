import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stock_market


def response_for(rows):
    payload = {"is_success": True, "data": {"data": rows, "meta": {"cursor": None}}}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(payload).encode()
    return Response()


class StockMarketTests(unittest.TestCase):
    def test_ihsg_live_quote_extracts_level_change_and_market_status(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                return json.dumps({"is_success": True, "data": {
                    "data": {"symbol": "IHSG", "name": "Index Harga Saham Gabungan", "date": "2026-10-23",
                             "last": 5425.78, "change": 37.88, "change_pct": 0.7, "prev_close": 5387.9,
                             "market_status": "open"},
                    "meta": {"as_of": "2026-10-23T10:55:00+07:00"},
                }}).encode()
        with patch.dict(os.environ, {"API_CO_ID_KEY": "test-key"}), \
             patch.object(stock_market.urllib.request, "urlopen", return_value=Response()) as urlopen:
            result = stock_market.analyze_ihsg()
        self.assertEqual(result["symbol"], "IHSG")
        self.assertEqual(result["last"], 5425.78)
        self.assertEqual(result["change_pct"], 0.7)
        self.assertEqual(result["market_status"], "open")
        self.assertIn("/idx/v2/chart/quote/IHSG", urlopen.call_args.args[0].full_url)

    def test_average_uses_daily_closes_and_api_co_id_auth(self):
        rows = [
            {"date": "2026-01-02", "close": 100},
            {"date": "2026-01-05", "close": 110},
            {"date": "2026-01-06", "close": 120},
        ]
        with patch.dict(os.environ, {"API_CO_ID_KEY": "test-key"}), \
             patch.object(stock_market.urllib.request, "urlopen", return_value=response_for(rows)) as urlopen:
            result = stock_market.analyze("ASII.JK", "2026-01-01", "2026-01-31")
        self.assertEqual(result["symbol"], "ASII")
        self.assertEqual(result["observations"], 3)
        self.assertAlmostEqual(result["average_close"], 110)
        self.assertAlmostEqual(result["period_change_pct"], 20)
        request = urlopen.call_args.args[0]
        self.assertIn("use.api.co.id/idx/v2/chart/daily/ASII", request.full_url)
        self.assertEqual(request.get_header("X-api-co-id"), "test-key")
        self.assertIn("bukan rekomendasi", stock_market.render(result))

    def test_compare_reports_rupiah_difference_and_percentage(self):
        start = {"date": "2025-01-02", "close": 100, "period_end": "2025-01-02"}
        end = {"date": "2025-02-03", "close": 125, "period_end": "2025-02-03"}
        rendered = stock_market.render_comparison("ASII", start, end)
        self.assertIn("naik **Rp 25.00 (25.00%)**", rendered)
        self.assertIn("2025-02-03", rendered)

    def test_missing_key_fails_without_network(self):
        with patch.dict(os.environ, {"API_CO_ID_KEY": ""}), patch.object(stock_market.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "API_CO_ID_KEY"):
                stock_market.analyze("ASII")
        urlopen.assert_not_called()

    def test_invalid_date_range_rejected(self):
        with patch.dict(os.environ, {"API_CO_ID_KEY": "test-key"}), patch.object(stock_market.urllib.request, "urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "sebelum"):
                stock_market.analyze("ASII", "2026-02-01", "2026-01-01")
        urlopen.assert_not_called()

    def test_random_universe_is_cached_and_sampled(self):
        rows = [{"symbol": "ASII"}, {"symbol": "BBCA"}, {"symbol": "TLKM"}]
        payload = {"is_success": True, "data": {"data": rows, "meta": {"cursor": None}}}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"API_CO_ID_KEY": "test-key"}), \
             patch.object(stock_market, "UNIVERSE_CACHE", Path(directory) / "universe.json"), \
             patch.object(stock_market.urllib.request, "urlopen", return_value=Response()) as urlopen, \
             patch.object(stock_market.random, "sample", return_value=["ASII", "TLKM"]):
            self.assertEqual(stock_market.random_symbols(2), ["ASII", "TLKM"])
            self.assertEqual(stock_market.random_symbols(2), ["ASII", "TLKM"])
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
