import unittest

import monitor


class MonitorDetailTests(unittest.TestCase):
    def test_describe_changes_reports_status_and_item_field(self):
        previous = {
            "systems": {
                "kasapink": {
                    "status": "OK",
                    "state": {"ingredients": [{"id": 1, "name": "Tepung", "stock": 10}]},
                }
            }
        }
        current = {
            "systems": {
                "kasapink": {
                    "status": "OK",
                    "state": {"ingredients": [{"id": 1, "name": "Tepung", "stock": 4}]},
                }
            }
        }
        changes = monitor.describe_changes(previous, current)
        self.assertTrue(any("ERP Henka" in item and "stock" in item and "10" in item and "4" in item for item in changes))

    def test_describe_changes_reports_added_and_removed_items(self):
        previous = {"systems": {"core_gateway": {"status": "OK", "snapshot": {"items": [{"id": "old"}]}}}}
        current = {"systems": {"core_gateway": {"status": "ERROR", "snapshot": {"items": [{"id": "new"}]}}}}
        changes = monitor.describe_changes(previous, current)
        self.assertTrue(any("status" in item for item in changes))
        self.assertTrue(any("ditambahkan" in item or "dihapus" in item for item in changes))


if __name__ == "__main__":
    unittest.main()
