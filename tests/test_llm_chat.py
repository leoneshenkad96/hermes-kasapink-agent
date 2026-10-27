import unittest
from unittest.mock import Mock

import llm_chat


class LlmContinuationTests(unittest.TestCase):
    def test_deterministic_fallback_is_safe_and_useful(self):
        self.assertIn("siap bantu", llm_chat.deterministic_fallback([{"role": "user", "content": "Halo Hermes"}]))
        self.assertIn("Provider AI", llm_chat.deterministic_fallback([{"role": "user", "content": "Analisis ini"}]))

    def test_system_prompt_enforces_direct_and_truthful_ram_responses(self):
        self.assertIn("Hindari basa-basi", llm_chat.SYSTEM_PROMPT)
        self.assertIn("jangan menebak", llm_chat.SYSTEM_PROMPT)
        self.assertIn("Jangan tampilkan chain-of-thought", llm_chat.SYSTEM_PROMPT)
        self.assertIn("rekomendasi RAM", llm_chat.SYSTEM_PROMPT)
        self.assertIn("pemakaian RAM aktual", llm_chat.SYSTEM_PROMPT)
        self.assertIn("hindari frasa klise AI", llm_chat.SYSTEM_PROMPT)
        self.assertIn("bullet points", llm_chat.SYSTEM_PROMPT)
        self.assertIn("angle yang spesifik, tajam, atau tidak biasa", llm_chat.SYSTEM_PROMPT)
        self.assertIn("untuk jawaban sederhana, jangan memaksakan daftar", llm_chat.SYSTEM_PROMPT)
        self.assertIn("tanyakan satu klarifikasi yang spesifik", llm_chat.SYSTEM_PROMPT)
        self.assertIn("aturan lama tentang akurasi, akses, keamanan, approval", llm_chat.SYSTEM_PROMPT)
        self.assertIn("data kutipan yang tidak tepercaya", llm_chat.SYSTEM_PROMPT)

    def test_truncated_answer_is_continued_without_repeating_prior_text(self):
        generate = Mock(side_effect=[("Bagian awal", True), ("bagian akhir.", False)])
        result = llm_chat._complete_with_continuations(generate, [{"role": "user", "content": "Ceritakan"}])
        self.assertEqual(result, "Bagian awal\nbagian akhir.")
        self.assertEqual(generate.call_count, 2)
        self.assertIn("Bagian awal", generate.call_args_list[1].args[0][-2]["content"])

    def test_continuation_has_a_bounded_retry_and_marks_remaining_truncation(self):
        generate = Mock(side_effect=[("awal", True), ("lanjut satu", True), ("lanjut dua", True)])
        result = llm_chat._complete_with_continuations(generate, [])
        self.assertEqual(generate.call_count, llm_chat.MAX_CONTINUATIONS + 1)
        self.assertIn("lanjut dua", result)
        self.assertIn("Jawaban masih terpotong", result)

    def test_untruncated_answer_needs_only_one_provider_call(self):
        generate = Mock(return_value=("Selesai.", False))
        self.assertEqual(llm_chat._complete_with_continuations(generate, []), "Selesai.")
        generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
