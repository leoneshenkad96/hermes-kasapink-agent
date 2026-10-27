import unittest
from unittest.mock import patch

import access_policy
import discord_bot
import network_health


class NetworkHealthTests(unittest.IsolatedAsyncioTestCase):
    def test_isp_request_routes_to_dedicated_read_only_handler(self):
        self.assertEqual(
            discord_bot.classify_command("Cek tiap ISP di core gateway, route aktif dan ping/loss"),
            ("isp_status", "core_gateway"),
        )
        self.assertTrue(access_policy.mikrotik_only_command("isp_status", "core_gateway"))

    def test_renderer_reports_only_observed_link_and_route_state(self):
        text = network_health.render_isp_status({
            "status": "OK",
            "snapshot": {
                "captured_at": "now",
                "interfaces": [
                    {"name": "ether1-ISP-Alpha", "running": "true", "disabled": "false"},
                    {"name": "ether2-ISP-Beta", "running": "false", "disabled": "false"},
                    {"name": "ether3-office", "running": "true", "disabled": "false"},
                ],
                "routes": [
                    {"dst-address": "0.0.0.0/0", "routing-table": "to-ISP-Alpha", "active": "true", "check-gateway": "ping", "immediate-gw": "192.0.2.1%ether1-ISP-Alpha"},
                    {"dst-address": "0.0.0.0/0", "routing-table": "to-ISP-Beta", "active": "false", "immediate-gw": "192.0.2.2%ether2-ISP-Beta"},
                ],
            },
        })
        self.assertIn("Alpha", text)
        self.assertIn("link UP", text)
        self.assertIn("to-ISP-Alpha` ✅ aktif · check-gateway ping", text)
        self.assertIn("Beta", text)
        self.assertIn("link DOWN", text)
        self.assertIn("to-ISP-Beta` ⛔ tidak aktif", text)
        self.assertIn("latency/packet-loss", text)
        self.assertNotIn("office", text)
        self.assertNotIn("192.0.2.1", text)

    async def test_dispatch_reports_actual_isp_snapshot_only_for_owner(self):
        snapshot = {"systems": {"core_gateway": {"status": "OK", "snapshot": {
            "interfaces": [{"name": "ether1-ISP-Alpha", "running": "true", "disabled": "false"}],
            "routes": [],
        }}}}
        with patch.object(discord_bot.monitor, "collect", return_value=snapshot) as collect, \
             patch.object(discord_bot, "audit"):
            response = await discord_bot.dispatch_text("Cek ISP core gateway", 246656697949618179)
        self.assertIn("Alpha", response)
        collect.assert_called_once_with(False)

        with patch.object(discord_bot.monitor, "collect") as collect, patch.object(discord_bot, "audit"):
            denied = await discord_bot.dispatch_text("Cek ISP core gateway", 669873388470140943)
        self.assertIn("hanya bisa dicek oleh Henka", denied)
        collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
