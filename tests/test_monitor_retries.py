import io
import os
import unittest
import urllib.error
from unittest.mock import patch

import monitor


class Response(io.BytesIO):
    status = 200


class RetryingReadOnlyCollectorTests(unittest.TestCase):
    def test_get_retries_transient_network_failure(self):
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise urllib.error.URLError("temporary DNS failure")
                return Response(b'{"status":"ok"}')

        opener = Opener()
        with patch.dict(os.environ, {"HERMES_GET_RETRIES": "3"}), patch.object(monitor.time, "sleep"):
            self.assertEqual(monitor.request_json("https://example.invalid/health", {}, opener=opener), {"status": "ok"})
        self.assertEqual(opener.calls, 2)

    def test_get_does_not_retry_auth_failure(self):
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

        opener = Opener()
        with patch.dict(os.environ, {"HERMES_GET_RETRIES": "3"}), patch.object(monitor.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                monitor.request_json("https://example.invalid/state", {}, opener=opener)
            caught.exception.close()
        self.assertEqual(opener.calls, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
