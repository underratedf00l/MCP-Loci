import unittest
from mcp_loci import db as db_module
from mcp_loci.server import remember, recall


class TestRecall(unittest.TestCase):

    def setUp(self):
        db_module.configure(":memory:")
        db_module.reset()

    def test_basic_keyword_search_returns_matching_memory(self):
        remember(
            name="redis_cache_config",
            type="project",
            description="Redis cache configuration",
            content="Configure Redis with maxmemory-policy allkeys-lru for caching",
        )
        results = recall(query="Redis cache")
        self.assertTrue(len(results) > 0)
        names = [r["name"] for r in results]
        self.assertIn("redis_cache_config", names)

    def test_type_filter_excludes_wrong_types(self):
        remember(
            name="user_type_memory",
            type="user",
            description="User type memory for filter test",
            content="This is a user memory for testing type filtering",
        )
        remember(
            name="project_type_memory",
            type="project",
            description="Project memory for filter test",
            content="This is a project memory for testing type filtering",
        )
        results = recall(query="memory filter test", type_filter="project")
        self.assertTrue(len(results) > 0, "Should return project memories")
        for r in results:
            self.assertEqual(r["type"], "project", "All results should be project type")

    def test_stale_filter_includes_fresh_memories(self):
        # Fresh memories (just created) should always pass the stale filter
        remember(
            name="fresh_stale_test",
            type="user",
            description="Fresh memory for stale test",
            content="This fresh memory should never be filtered as stale",
        )
        results = recall(query="fresh memory stale")
        names = [r["name"] for r in results]
        self.assertIn("fresh_stale_test", names,
                      "Freshly created memory should not be filtered as stale")

    def test_include_stale_returns_at_least_as_many_results(self):
        remember(
            name="stale_include_test",
            type="reference",
            description="Memory for include stale comparison",
            content="Testing include stale flag behavior returns expected results",
        )
        normal = recall(query="stale include comparison", include_stale=False)
        with_stale = recall(query="stale include comparison", include_stale=True)
        self.assertGreaterEqual(len(with_stale), len(normal))

    def test_use_count_increments_on_recall(self):
        remember(
            name="use_count_tracker",
            type="reference",
            description="Use count increment test",
            content="This memory tracks use count increments on recall",
        )
        recall(query="use count increment")
        recall(query="use count increment")
        conn = db_module.get_conn()
        row = conn.execute(
            "SELECT use_count FROM memories WHERE name = ?",
            ("use_count_tracker",)
        ).fetchone()
        self.assertGreaterEqual(row["use_count"], 2,
                                "use_count should increment each time memory is recalled")

    def test_match_reason_present_on_all_results(self):
        remember(
            name="match_reason_check",
            type="feedback",
            description="Match reason presence test",
            content="Every recalled memory should have a human-readable match reason",
        )
        results = recall(query="match reason presence")
        self.assertTrue(len(results) > 0)
        for r in results:
            self.assertIn("match_reason", r)
            self.assertIsInstance(r["match_reason"], str)
            self.assertGreater(len(r["match_reason"]), 0)


if __name__ == "__main__":
    unittest.main()
