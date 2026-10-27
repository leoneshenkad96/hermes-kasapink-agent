import unittest
from unittest.mock import patch

import discord_connectivity


class DiscordConnectivityTests(unittest.TestCase):
    def test_dns_preflight_is_read_only_and_reports_partial_failure(self):
        with patch.object(discord_connectivity, "resolve_hosts", return_value={"discord.com": True, "gateway.discord.gg": False}), \
             patch.object(discord_connectivity.time, "sleep") as sleep:
            result = discord_connectivity.wait_for_dns(attempts=3)
        self.assertFalse(result["ready"])
        self.assertEqual(result["attempt"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_dns_preflight_stops_when_ready(self):
        with patch.object(discord_connectivity, "resolve_hosts", return_value={"discord.com": True, "gateway.discord.gg": True}), \
             patch.object(discord_connectivity.time, "sleep") as sleep:
            result = discord_connectivity.wait_for_dns(attempts=3)
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempt"], 1)
        sleep.assert_not_called()
