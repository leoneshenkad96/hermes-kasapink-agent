import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import discord_bot


class IdleAlertTests(unittest.TestCase):
    def test_idle_alert_waits_three_hours_then_repeats_every_three_hours(self):
        start = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        with patch.object(discord_bot, "IDLE_ALERT_MINUTES", 180):
            discord_bot.reset_idle_timer(start)
            self.assertFalse(discord_bot.idle_alert_due(start + timedelta(minutes=179)))
            self.assertTrue(discord_bot.idle_alert_due(start + timedelta(minutes=180)))
            self.assertFalse(discord_bot.idle_alert_due(start + timedelta(minutes=359)))
            self.assertTrue(discord_bot.idle_alert_due(start + timedelta(minutes=360)))

    def test_activity_restarts_idle_window(self):
        start = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        activity = start + timedelta(minutes=45)
        with patch.object(discord_bot, "IDLE_ALERT_MINUTES", 180):
            discord_bot.reset_idle_timer(start)
            discord_bot.reset_idle_timer(activity)
            self.assertFalse(discord_bot.idle_alert_due(activity + timedelta(minutes=179)))
            self.assertTrue(discord_bot.idle_alert_due(activity + timedelta(minutes=180)))


if __name__ == "__main__":
    unittest.main()
