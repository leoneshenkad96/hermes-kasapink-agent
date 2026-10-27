import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import discord_bot


class DailyScheduleTests(unittest.TestCase):
    def test_hermes_build_status_reports_runtime_features_and_snapshot_freshness(self):
        rendered = discord_bot.hermes_build_status(
            {"captured_at": "2026-10-23T04:01:50+00:00", "systems": {
                "kasapink": {"status": "OK"},
                "core_gateway": {"status": "OK"},
                "lan_controller": {"status": "ERROR"},
            }},
            "Running",
            True,
            42.0,
        )
        self.assertIn("status build", rendered)
        self.assertIn("Running", rendered)
        self.assertIn("ping 42 ms", rendered)
        self.assertIn("ERP Henka: ✅ **OK**", rendered)
        self.assertIn("LAN Controller: 🚨 **ERROR**", rendered)
        self.assertIn("bukan live refresh", rendered)
        self.assertNotIn("Kasapink", rendered)

    def test_status_uses_personal_erp_label_not_project_brand(self):
        rendered = discord_bot.status_text({"systems": {
            "kasapink": {"status": "OK"},
            "core_gateway": {"status": "OK"},
            "lan_controller": {"status": "OK"},
        }})
        self.assertIn("ERP Henka", rendered)
        self.assertIn("Hermes Bossun", rendered)
        self.assertNotIn("kasapink", rendered.lower())

    def test_claim_prevents_duplicate_daily_blast(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "daily.json"
            with patch.object(discord_bot, "DAILY_REPORT_FILE", target):
                self.assertTrue(discord_bot._claim_daily_report("2026-10-23"))
                self.assertFalse(discord_bot._claim_daily_report("2026-10-23"))
                discord_bot._finish_daily_report("2026-10-23", "SENT")
                self.assertFalse(discord_bot._claim_daily_report("2026-10-23"))

    def test_natural_stock_query_routes_to_market_lookup(self):
        self.assertEqual(discord_bot.classify_command("cek saham BBCA"), ("stock_lookup", "BBCA"))
        self.assertEqual(discord_bot.classify_command("analisis saham TLKM"), ("stock_lookup", "TLKM"))

    def test_natural_historical_average_query(self):
        self.assertEqual(discord_bot.classify_command("berapa rata-rata saham ASII setahun"), ("stock_average", "ASII"))

    def test_natural_date_comparison_query(self):
        self.assertEqual(
            discord_bot.classify_command("Apakah saham ASII naik dari 2025-01-01 sampai 2025-06-30?"),
            ("stock_compare", "ASII|2025-01-01|2025-06-30"),
        )
        self.assertEqual(
            discord_bot.classify_command("ASII naik atau turun dari 10 september 2026 sampe 16 septemberl 2026?"),
            ("stock_compare", "ASII|2026-09-10|2026-09-16"),
        )

    def test_stock_digest_schedules_after_both_idx_sessions(self):
        self.assertEqual(discord_bot.stock_session_for_time(datetime(2026, 10, 26, 12, 5)), "morning")
        self.assertIsNone(discord_bot.stock_session_for_time(datetime(2026, 10, 26, 11, 35)))
        self.assertEqual(discord_bot.stock_session_for_time(datetime(2026, 10, 23, 11, 35)), "morning")
        self.assertEqual(discord_bot.stock_session_for_time(datetime(2026, 10, 23, 16, 20)), "close")
        self.assertIsNone(discord_bot.stock_session_for_time(datetime(2026, 10, 25, 16, 20)))

    def test_stock_session_claim_prevents_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "stock.json"
            with patch.object(discord_bot, "STOCK_REPORT_FILE", target):
                self.assertTrue(discord_bot._claim_stock_report("2026-10-23", "morning"))
                self.assertFalse(discord_bot._claim_stock_report("2026-10-23", "morning"))
                discord_bot._finish_stock_report("2026-10-23", "morning", "SENT")
                self.assertFalse(discord_bot._claim_stock_report("2026-10-23", "morning"))
                self.assertTrue(discord_bot._claim_stock_report("2026-10-23", "close"))


if __name__ == "__main__":
    unittest.main()
