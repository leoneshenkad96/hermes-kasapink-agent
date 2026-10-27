from __future__ import annotations

import json

import message_dedup
import discord_bot


def test_claim_suppresses_same_message_id(tmp_path, monkeypatch):
    path = tmp_path / "dedup.json"
    monkeypatch.setattr(message_dedup, "STATE_FILE", path)
    assert message_dedup.claim(123, 10, 20, "help", now=1000)
    assert not message_dedup.claim(123, 10, 20, "help", now=1001)


def test_claim_suppresses_repeated_content_then_allows_after_cooldown(tmp_path, monkeypatch):
    path = tmp_path / "dedup.json"
    monkeypatch.setattr(message_dedup, "STATE_FILE", path)
    monkeypatch.setattr(message_dedup, "CONTENT_COOLDOWN_SECONDS", 45)
    assert message_dedup.claim(1, 10, 20, "Sekarang lu bisa apa aja?", now=1000)
    assert not message_dedup.claim(2, 10, 20, "  sekarang   LU BISA APA AJA? ", now=1001)
    assert message_dedup.claim(3, 10, 20, "Sekarang lu bisa apa aja?", now=1046)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 4


def test_help_menu_question_is_deterministic():
    assert discord_bot.classify_command("Daftar menu help lu ada apa aja?") == ("help", "")
