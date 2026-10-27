import unittest

import daily_report


class DailyReportTests(unittest.TestCase):
    def test_summary_includes_operational_kpis_and_no_router_sensitive_details(self):
        snapshot = {
            "captured_at": "2026-10-23T00:00:00+00:00",
            "systems": {
                "kasapink": {"status": "OK", "state": {
                    "today": {"date": "2026-10-23", "revenue": 100000, "costOfGoodsSold": 40000, "grossProfit": 60000, "purchases": 10000},
                    "ingredients": [{"name": "Tepung", "stock": 1, "minStock": 2, "unit": "kg"}],
                    "products": [], "preparations": [],
                }},
                "core_gateway": {"status": "OK", "snapshot": {"resource": [{"cpu-load": "17", "free-memory": "1048576", "total-memory": "2097152"}], "interfaces": [{"name": "secret-interface", "running": "false", "disabled": "false"}]}},
                "lan_controller": {"status": "ERROR"},
            },
        }
        rendered = daily_report.render(snapshot)
        self.assertIn("omzet **Rp 100,000**", rendered)
        self.assertIn("Tepung", rendered)
        self.assertIn("Core Gateway", rendered)
        self.assertIn("LAN Controller: 🚨 **ERROR**", rendered)
        self.assertNotIn("secret-interface", rendered)

    def test_daily_report_includes_bounded_recent_anomaly_summary(self):
        rendered = daily_report.render({"captured_at": "2026-10-23T00:00:00+00:00", "systems": {}}, [
            {"severity": "MEDIUM", "system": "kasapink", "message": "Biaya naik"},
        ])
        self.assertIn("Anomali 24 jam terakhir", rendered)
        self.assertIn("Biaya naik", rendered)
        self.assertIn("tidak ada temuan rule-based", daily_report.render({"systems": {}}))

    def test_stock_digest_is_separate_and_historical(self):
        rendered = daily_report.render_stock([("ASII", {
            "date": "2026-10-22", "close": 6000, "previous_close": 5900, "change_pct": 1.69,
        })])
        self.assertIn("ASII", rendered)
        self.assertIn("Rp 6,000.00", rendered)
        self.assertIn("gainers/losers", rendered)

    def test_morning_stock_report_is_labeled_provisional(self):
        rendered = daily_report.render_stock([], session="morning")
        self.assertIn("Sesi I", rendered)
        self.assertIn("snapshot sementara", rendered)

    def test_stock_report_includes_live_ihsg_direction_level_and_change(self):
        rendered = daily_report.render_stock([], ihsg={
            "last": 5425.78, "change": 37.88, "change_pct": 0.7,
            "previous_close": 5387.90, "date": "2026-10-23", "market_status": "open",
            "as_of": "2026-10-23T10:55:00+07:00",
        })
        self.assertIn("Tren IHSG", rendered)
        self.assertIn("5,425.78", rendered)
        self.assertIn("menguat", rendered)
        self.assertIn("37.88 poin (0.70%)", rendered)
        self.assertIn("open", rendered)


if __name__ == "__main__":
    unittest.main()
