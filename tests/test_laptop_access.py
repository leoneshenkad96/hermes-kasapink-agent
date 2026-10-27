import unittest
from unittest.mock import patch

import discord_bot


class LaptopAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_chatgpt_start_and_recovery_commands_are_distinct(self):
        self.assertEqual(discord_bot.classify_command("Hermes, aktifkan ChatGPT"), ("local_action_proposal", "start_chatgpt"))
        self.assertEqual(discord_bot.classify_command("Hermes, restart ChatGPT"), ("local_action_proposal", "restart_chatgpt"))
        self.assertEqual(discord_bot.classify_command("Codex tidak aktif"), ("local_action_proposal", "restart_chatgpt"))
        self.assertEqual(discord_bot.classify_command("Codex error"), ("local_action_proposal", "restart_chatgpt"))

    def test_chatgpt_status_routes_to_read_only_diagnostic(self):
        self.assertEqual(discord_bot.classify_command("cek status ChatGPT"), ("laptop_status", "chatgpt"))

    def test_storage_wording_routes_to_disk_diagnostic(self):
        self.assertEqual(discord_bot.classify_command("Storage laptop gue sisa berapa GB?"), ("laptop_status", "disk"))

    async def test_registered_non_owner_cannot_read_laptop_diagnostics(self):
        for user_id in (669873388470140943, 756343447232708730, 1527267906986053722):
            with patch.object(discord_bot.local_runner, "diagnose") as diagnose, patch.object(discord_bot, "audit"):
                response = await discord_bot.dispatch_text("cek CPU usage gue", user_id)
            self.assertIn("hanya bisa diakses Henka", response)
            diagnose.assert_not_called()

    async def test_owner_can_read_laptop_diagnostics(self):
        with patch.object(discord_bot.local_runner, "diagnose", return_value={"status": "OK", "usage": {"cpu_usage_percent": 10, "memory_total_gb": 8, "memory_free_gb": 4}, "cpu_count": 4, "os": "Windows"}), patch.object(discord_bot, "audit"):
            response = await discord_bot.dispatch_text("cek CPU usage gue", 246656697949618179)
        self.assertIn("CPU usage", response)


if __name__ == "__main__":
    unittest.main()
