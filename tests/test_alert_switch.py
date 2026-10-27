from __future__ import annotations

import discord_bot


def test_alert_delivery_can_be_disabled(monkeypatch):
    monkeypatch.setattr(discord_bot, "ALERTS_ENABLED", False)
    assert not discord_bot.alerts_enabled()


def test_alert_delivery_can_be_enabled(monkeypatch):
    monkeypatch.setattr(discord_bot, "ALERTS_ENABLED", True)
    assert discord_bot.alerts_enabled()
