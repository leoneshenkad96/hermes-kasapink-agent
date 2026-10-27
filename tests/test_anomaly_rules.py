import unittest

import anomaly_rules


class AnomalyRuleTests(unittest.TestCase):
    def test_detects_erp_unavailable_transition(self):
        previous = {"systems": {"kasapink": {"status": "OK", "state": {}}}}
        current = {"systems": {"kasapink": {"status": "ERROR", "error": "HTTPError"}}}
        found = anomaly_rules.detect(previous, current)
        self.assertTrue(any(item["rule_id"] == "SYSTEM_UNAVAILABLE" and item["system"] == "kasapink" for item in found))

    def test_detects_high_router_cpu_and_low_memory(self):
        before = {"systems": {"core_gateway": {"status": "OK", "snapshot": {"resource": [{"cpu-load": "10", "free-memory": "900", "total-memory": "1000"}]}}}}
        after = {"systems": {"core_gateway": {"status": "OK", "snapshot": {"resource": [{"cpu-load": "95", "free-memory": "50", "total-memory": "1000"}]}}}}
        found = anomaly_rules.detect(before, after)
        rules = {item["rule_id"] for item in found}
        self.assertIn("ROUTER_CPU_HIGH", rules)
        self.assertIn("ROUTER_MEMORY_LOW", rules)

    def test_malformed_numeric_values_are_ignored_not_crash(self):
        before = {"systems": {"kasapink": {"status": "OK", "state": {"ingredients": [{"id": 1, "stock": "n/a"}]}}}}
        after = {"systems": {"kasapink": {"status": "OK", "state": {"ingredients": [{"id": 1, "stock": None}]}}}}
        self.assertEqual(anomaly_rules.detect(before, after), [])

    def test_persistent_high_cpu_does_not_repeat_threshold_alert(self):
        snapshot = {"systems": {"core_gateway": {"status": "OK", "snapshot": {"resource": [{"cpu-load": "95", "free-memory": "500", "total-memory": "1000"}]}}}}
        self.assertFalse(any(item["rule_id"] == "ROUTER_CPU_HIGH" for item in anomaly_rules.detect(snapshot, snapshot)))


if __name__ == "__main__":
    unittest.main()
