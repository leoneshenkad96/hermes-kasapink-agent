import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import alert_dedup


class AlertDedupTests(TestCase):
    def test_duplicate_claim_is_suppressed_and_state_is_persistent(self):
        with TemporaryDirectory() as directory:
            state = Path(directory) / "alert-dedup.json"
            start = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)
            with patch.object(alert_dedup, "STATE_FILE", state):
                self.assertTrue(alert_dedup.claim("same", now=start, cooldown_minutes=180))
                self.assertFalse(alert_dedup.claim("same", now=start + timedelta(minutes=179), cooldown_minutes=180))
                self.assertTrue(alert_dedup.claim("same", now=start + timedelta(minutes=180), cooldown_minutes=180))
                self.assertIn("same", json.loads(state.read_text(encoding="utf-8"))["claims"])

    def test_different_fingerprint_is_not_blocked_by_existing_alert(self):
        with TemporaryDirectory() as directory:
            with patch.object(alert_dedup, "STATE_FILE", Path(directory) / "alert-dedup.json"):
                now = datetime(2026, 10, 26, tzinfo=timezone.utc)
                self.assertTrue(alert_dedup.claim("error-a", now=now, cooldown_minutes=180))
                self.assertTrue(alert_dedup.claim("recovery-a", now=now, cooldown_minutes=180))
