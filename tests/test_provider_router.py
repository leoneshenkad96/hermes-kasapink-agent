import os
import unittest
from unittest.mock import patch

import provider_router


class ProviderRouterTests(unittest.TestCase):
    def setUp(self):
        provider_router.reset_state()

    def tearDown(self):
        provider_router.reset_state()

    def test_configured_provider_names_exclude_ollama_and_claude(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "OPENROUTER_API_KEY": "z"}, clear=False):
            names = provider_router.configured_providers()
        self.assertEqual(names[:3], ["groq", "gemini", "openrouter"])
        self.assertNotIn("ollama", names)
        self.assertNotIn("claude", names)

    def test_routes_to_first_available_provider(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "HERMES_PROVIDER_ORDER": "groq,gemini"}, clear=False), patch.object(provider_router, "_call", return_value=("ok", False)) as call:
            result = provider_router.generate([{"role": "user", "content": "hi"}], 20)
        self.assertEqual(result, ("ok", "groq", False))
        call.assert_called_once()

    def test_falls_back_and_opens_circuit_after_provider_error(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "HERMES_PROVIDER_ORDER": "groq,gemini", "HERMES_PROVIDER_RETRIES": "0"}, clear=False), patch.object(provider_router, "_call", side_effect=[RuntimeError("secret must not leak"), ("gemini ok", False)]):
            result = provider_router.generate([{"role": "user", "content": "hi"}], 20)
        self.assertEqual(result[1], "gemini")
        self.assertGreater(provider_router._state("groq").cooldown_until, 0)

    def test_daily_budget_skips_provider(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "HERMES_PROVIDER_ORDER": "groq,gemini", "HERMES_PROVIDER_MAX_CALLS_PER_DAY": "1"}, clear=False), patch.object(provider_router, "_call", return_value=("ok", False)) as call:
            provider_router.generate([{"role": "user", "content": "one"}], 20)
            provider_router.generate([{"role": "user", "content": "two"}], 20)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(provider_router._state("groq").calls_today, 1)
        self.assertEqual(provider_router._state("gemini").calls_today, 1)

    def test_cloudflare_requires_account_binding(self):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "x"}, clear=True):
            self.assertNotIn("cloudflare", provider_router.configured_providers())


if __name__ == "__main__":
    unittest.main()
