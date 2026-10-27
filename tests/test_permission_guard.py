import unittest

import permission_guard


class PermissionGuardTests(unittest.TestCase):
    def test_read_only_commands_have_read_only_level(self):
        self.assertEqual(permission_guard.level_for("status"), permission_guard.PermissionLevel.READ_ONLY)
        self.assertEqual(permission_guard.level_for("product_search"), permission_guard.PermissionLevel.READ_ONLY)

    def test_proposal_commands_are_draft_until_approved(self):
        self.assertEqual(permission_guard.level_for("local_action_proposal"), permission_guard.PermissionLevel.DRAFT)
        self.assertEqual(permission_guard.level_for("template"), permission_guard.PermissionLevel.DRAFT)
        self.assertEqual(
            permission_guard.level_for("template", approved=True),
            permission_guard.PermissionLevel.EXECUTE,
        )

    def test_read_only_principal_cannot_request_draft_or_execute(self):
        self.assertFalse(permission_guard.can_request("template", permission_guard.PermissionLevel.READ_ONLY))
        self.assertFalse(permission_guard.can_request("approve", permission_guard.PermissionLevel.DRAFT))
        self.assertTrue(permission_guard.can_request("status", permission_guard.PermissionLevel.READ_ONLY))


if __name__ == "__main__":
    unittest.main()
