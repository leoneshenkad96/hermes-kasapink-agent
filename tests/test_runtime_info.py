import os
from unittest import TestCase
from unittest.mock import patch

import discord_bot
import runtime_info


class RuntimeInfoTests(TestCase):
    def test_identity_questions_are_deterministically_routed(self):
        for question in (
            "hermes --version",
            "hermes model",
            "Hermes dibuild pake apa aja, model apa, versi berapa?",
            "info Hermes",
        ):
            self.assertEqual(discord_bot.classify_command(question)[0], "runtime_info")

    def test_render_has_runtime_facts_without_secrets(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "secret-test-value", "GROQ_MODEL": "test-model"}, clear=False):
            response = runtime_info.render()
        self.assertIn("test-model", response)
        self.assertIn("Token dan API key tidak ditampilkan", response)
        self.assertNotIn("secret-test-value", response)
        self.assertIn("Hermes CLI lokal", response)
