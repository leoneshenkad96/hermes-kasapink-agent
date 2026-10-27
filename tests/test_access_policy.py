import unittest

import access_policy


class AccessPolicyTests(unittest.TestCase):
    def test_parse_user_ids(self):
        self.assertEqual(access_policy.parse_user_ids("123, 456;not-an-id"), {"123", "456"})

    def test_privileged_roles_default_to_owner_and_allow_explicit_members(self):
        self.assertEqual(access_policy.role_user_ids("123", ""), {"123"})
        self.assertEqual(access_policy.role_user_ids("123", "456;bad"), {"123", "456"})

    def test_mikrotik_queries_are_owner_scoped(self):
        self.assertTrue(access_policy.mikrotik_only_command("diff"))
        self.assertTrue(access_policy.mikrotik_only_command("status", "core_gateway"))
        self.assertTrue(access_policy.mikrotik_only_command("status", "lan_controller"))
        self.assertTrue(access_policy.mikrotik_only_command("status", "all"))
        self.assertFalse(access_policy.mikrotik_only_command("status", "kasapink"))
        self.assertFalse(access_policy.mikrotik_only_command("stock_lookup", ""))


if __name__ == "__main__":
    unittest.main()
