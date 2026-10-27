import unittest
from unittest.mock import patch

import discord_bot
import resource_usage


class ResourceUsageTests(unittest.IsolatedAsyncioTestCase):
    def test_ram_usage_phrase_routes_to_actual_metrics(self):
        self.assertEqual(discord_bot.classify_command("RAM usage gue, server ERP, dan MikroTik"), ("resource_usage", "all"))
        self.assertEqual(discord_bot.classify_command("RAM MikroTik berapa?"), ("resource_usage", "mikrotik"))
        self.assertEqual(discord_bot.classify_command("berapa RAM yang dibutuhkan server ERP?"), ("unknown", "berapa ram yang dibutuhkan server erp?"))

    def test_render_uses_observed_values_and_never_invents_erp_host_ram(self):
        text = resource_usage.render(
            laptop={"status": "OK", "usage": {"memory_total_gb": 24, "memory_free_gb": 8}},
            snapshot={"captured_at": "2026-10-23T06:00:00+00:00", "systems": {
                "core_gateway": {"status": "OK", "snapshot": {"resource": [{"free-memory": "1610612736", "total-memory": "2147483648"}]}},
                "lan_controller": {"status": "ERROR"},
            }},
            can_see_laptop=True, can_see_mikrotik=True,
        )
        self.assertIn("16.00/24.00 GB terpakai (66.7%)", text)
        self.assertIn("512/2048 MB terpakai (25.0%)", text)
        self.assertIn("LAN Controller: data RAM tidak tersedia (status ERROR)", text)
        self.assertIn("API ERP yang dikonfigurasi tidak menyediakan metrik RAM host", text)
        self.assertNotIn("4–8 GB", text)

    async def test_owner_resource_query_reads_local_and_router_metrics(self):
        snapshot = {"captured_at": "now", "systems": {}}
        with patch.object(discord_bot.local_runner, "diagnose", return_value={"status": "OK", "usage": {"memory_total_gb": 16, "memory_free_gb": 6}}) as laptop, \
             patch.object(discord_bot.monitor, "collect", return_value=snapshot) as collect, \
             patch.object(discord_bot, "audit"):
            response = await discord_bot.dispatch_text("RAM usage gue, server ERP, dan MikroTik", 246656697949618179)
        self.assertIn("Pemakaian RAM aktual", response)
        self.assertIn("Server ERP", response)
        laptop.assert_called_once_with("system")
        collect.assert_called_once_with(False)

    async def test_non_owner_gets_access_boundaries_without_local_or_router_reads(self):
        with patch.object(discord_bot.local_runner, "diagnose") as laptop, \
             patch.object(discord_bot.monitor, "collect") as collect, \
             patch.object(discord_bot, "audit"):
            response = await discord_bot.dispatch_text("RAM usage semua", 669873388470140943)
        self.assertIn("hanya Henka", response)
        self.assertIn("hanya Henka yang bisa melihat metrik router", response)
        laptop.assert_not_called()
        collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
