import unittest
from mcp_loci import db as db_module
from mcp_loci.server import remember, recall


class TestRemember(unittest.TestCase):

    def setUp(self):
        db_module.configure(":memory:")
        db_module.reset()

    def test_create_new_memory_returns_created(self):
        result = remember(
            name="user_dark_mode_pref",
            type="user",
            description="User prefers dark mode",
            content="User has expressed strong preference for dark mode interfaces",
        )
        self.assertTrue(result["stored"])
        self.assertEqual(result["action"], "created")
        self.assertIsNotNone(result["id"])
        self.assertIsInstance(result["conflicts"], list)

    def test_upsert_same_name_returns_updated_and_changes_content(self):
        remember(
            name="upsert_target",
            type="feedback",
            description="Original description",
            content="Original content here",
        )
        result = remember(
            name="upsert_target",
            type="feedback",
            description="Updated description",
            content="Updated content here",
        )
        self.assertEqual(result["action"], "updated")

        results = recall(query="Updated content")
        matching = [r for r in results if r["name"] == "upsert_target"]
        self.assertTrue(len(matching) > 0, "Updated memory should be recallable")
        self.assertEqual(matching[0]["content"], "Updated content here")

    def test_pinned_flag_persists_and_yields_confidence_1(self):
        remember(
            name="pinned_reference",
            type="reference",
            description="Critical pinned reference",
            content="Must-have information that should always surface",
            pinned=True,
        )
        results = recall(query="Critical pinned reference")
        matching = [r for r in results if r["name"] == "pinned_reference"]
        self.assertTrue(len(matching) > 0)
        self.assertTrue(matching[0]["pinned"])
        self.assertEqual(matching[0]["confidence_score"], 1.0)

    def test_conflict_detection_returns_overlapping_matches(self):
        remember(
            name="python_project_config",
            type="project",
            description="Python project configuration setup",
            content="Use pyproject.toml for Python project configuration",
        )
        result = remember(
            name="python_setup_guide",
            type="project",
            description="Python project setup guide",
            content="Guide for setting up Python project structure",
        )
        self.assertIsInstance(result["conflicts"], list)
        # Should detect the overlapping python/project keywords
        conflict_names = [c["name"] for c in result["conflicts"]]
        self.assertIn("python_project_config", conflict_names)


if __name__ == "__main__":
    unittest.main()
