import unittest
from mcp_loci import db as db_module
from mcp_loci.server import remember, recall, forget


class TestForget(unittest.TestCase):

    def setUp(self):
        db_module.configure(":memory:")
        db_module.reset()

    def test_soft_delete_sets_status_archived(self):
        result = remember(
            name="archive_target",
            type="project",
            description="Memory to be archived",
            content="This memory will be soft-deleted via forget",
        )
        memory_id = result["id"]

        forget_result = forget(memory_id)
        self.assertTrue(forget_result["archived"])

        conn = db_module.get_conn()
        row = conn.execute(
            "SELECT status FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        self.assertEqual(row["status"], "archived")

    def test_pinned_memory_blocked_without_force(self):
        remember(
            name="pinned_guard_test",
            type="reference",
            description="Pinned memory that resists archiving",
            content="This pinned memory should block forget without force=True",
            pinned=True,
        )
        result = forget("pinned_guard_test")
        self.assertFalse(result["archived"])
        self.assertTrue(result["was_pinned"])
        self.assertIn("force=True", result["reason"])

    def test_pinned_memory_archived_with_force(self):
        remember(
            name="pinned_force_test",
            type="reference",
            description="Pinned memory that can be force-archived",
            content="This pinned memory can be archived with force=True",
            pinned=True,
        )
        result = forget("pinned_force_test", force=True)
        self.assertTrue(result["archived"])
        self.assertTrue(result["was_pinned"])
        self.assertIsNone(result["reason"])

    def test_forget_by_name_works(self):
        remember(
            name="forget_by_name",
            type="user",
            description="Test forgetting by name not id",
            content="This memory is forgotten using its name as lookup key",
        )
        result = forget("forget_by_name")
        self.assertTrue(result["archived"])

    def test_forgotten_memory_excluded_from_recall(self):
        remember(
            name="recall_exclusion_test",
            type="project",
            description="Memory excluded after forget",
            content="After archiving this memory should not appear in recall results",
        )
        before = recall(query="excluded after forget archiving")
        self.assertIn("recall_exclusion_test", [r["name"] for r in before])

        forget("recall_exclusion_test")

        after = recall(query="excluded after forget archiving")
        self.assertNotIn("recall_exclusion_test", [r["name"] for r in after])

    def test_forget_nonexistent_returns_not_found(self):
        result = forget("nonexistent_memory_xyz_123")
        self.assertFalse(result["archived"])
        self.assertEqual(result["reason"], "not found")


if __name__ == "__main__":
    unittest.main()
