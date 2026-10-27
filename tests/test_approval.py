import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval


class ApprovalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = approval.APPROVAL_FILE
        approval.APPROVAL_FILE = Path(self.temp.name) / "approvals.json"
        self.env = patch.dict(os.environ, {
            "KASAPINK_OPERATOR_USERNAME": "test-user",
            "KASAPINK_OPERATOR_PASSWORD": "test-password",
            "KASAPINK_BASE_URL": "https://example.invalid",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        approval.APPROVAL_FILE = self.old_path
        self.temp.cleanup()

    def test_remote_timeout_becomes_unknown_and_cannot_be_replayed(self):
        record = approval.create(requester_id=7, requester_name="tester", method="POST",
                                 path="/api/erp/ingredients", body={"name": "test"})
        class BrokenOpener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise TimeoutError("simulated timeout")
        opener = BrokenOpener()
        with patch.object(approval.monitor, "login_kasapink", return_value=(opener, {})):
            with self.assertRaises(TimeoutError):
                approval.approve_and_execute(record["id"], 7)
            stored = json.loads(approval.APPROVAL_FILE.read_text(encoding="utf-8"))[record["id"]]
            self.assertEqual(stored["status"], "UNKNOWN")
            with self.assertRaisesRegex(ValueError, "UNKNOWN"):
                approval.approve_and_execute(record["id"], 7)
        self.assertEqual(opener.calls, 1)

    def test_automatic_low_risk_write_is_audited_and_executes_once(self):
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                return io.BytesIO(b'{"id":42,"name":"Gula Aren"}')
        opener = Opener()
        with patch.object(approval.monitor, "login_kasapink", return_value=(opener, {})):
            record, result = approval.execute_automatically(
                requester_id=12, requester_name="Pinky", method="POST",
                path="/api/erp/ingredients", body={"name": "Gula Aren"},
                policy_reason="test policy",
            )
        stored = json.loads(approval.APPROVAL_FILE.read_text(encoding="utf-8"))[record["id"]]
        self.assertEqual(record["approval_mode"], "AUTOMATIC")
        self.assertEqual(record["automatic_reason"], "test policy")
        self.assertEqual(stored["status"], "EXECUTED")
        self.assertEqual(result["id"], 42)
        self.assertEqual(opener.calls, 1)

    def test_automatic_timeout_is_marked_unknown(self):
        class BrokenOpener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise TimeoutError("simulated timeout")
        opener = BrokenOpener()
        with patch.object(approval.monitor, "login_kasapink", return_value=(opener, {})):
            with self.assertRaises(approval.AutomaticExecutionError) as caught:
                approval.execute_automatically(
                    requester_id=12, requester_name="Pinky", method="POST",
                    path="/api/erp/ingredients", body={"name": "Gula Aren"},
                    policy_reason="test policy",
                )
        stored = json.loads(approval.APPROVAL_FILE.read_text(encoding="utf-8"))[caught.exception.approval_id]
        self.assertEqual(stored["status"], "UNKNOWN")
        self.assertEqual(opener.calls, 1)

    def test_status_is_requester_scoped_and_omits_submitted_body(self):
        record = approval.create(requester_id=7, requester_name="tester", method="POST",
                                 path="/api/erp/ingredients", body={"name": "private value"})
        with self.assertRaises(PermissionError):
            approval.get_status(record["id"], 8)
        visible = approval.get_status(record["id"], 7)
        self.assertEqual(visible["status"], "PENDING")
        self.assertNotIn("body", visible)


if __name__ == "__main__":
    unittest.main()
