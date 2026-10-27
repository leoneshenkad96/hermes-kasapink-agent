import tempfile
import unittest
from pathlib import Path

import repo_context


class SecretRedactionTests(unittest.TestCase):
    def test_json_markdown_env_and_camelcase_secret_lines_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.md"
            source.write_text(
                '"apiKey": "secret-value"\n| token | secret-value |\nPASSWORD=secret-value\n'
                'databaseUrl: postgres://user:pass@example/db\nnormal: safe\n',
                encoding="utf-8",
            )
            cleaned = repo_context._safe_text(source)
        self.assertNotIn("secret-value", cleaned)
        self.assertNotIn("postgres://", cleaned)
        self.assertIn("normal: safe", cleaned)
        self.assertEqual(cleaned.count("[sensitive configuration line omitted]"), 4)


if __name__ == "__main__":
    unittest.main()
