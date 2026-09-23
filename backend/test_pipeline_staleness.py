"""Unit tests for `pipeline_staleness.compute_staleness`.

Run directly (no pytest dependency required):

    .\\.venv\\Scripts\\python.exe backend\\test_pipeline_staleness.py
"""

import unittest

from pipeline_staleness import compute_staleness

T1 = "2026-01-01T00:00:00+00:00"  # import
T2 = "2026-01-01T01:00:00+00:00"  # references
T3 = "2026-01-01T02:00:00+00:00"  # knowledge
T4 = "2026-01-01T03:00:00+00:00"  # architecture
T5 = "2026-01-01T04:00:00+00:00"  # causal
T6 = "2026-01-01T05:00:00+00:00"  # collaboration
T7 = "2026-01-01T06:00:00+00:00"  # algorithms
T8 = "2026-01-01T07:00:00+00:00"  # embeddings


def base_state():
    """All eight timestamps present and in correct dependency order."""
    return {
        "last_import_at": T1,
        "last_extraction_at": T2,
        "last_layer_build_at": T3,
        "last_architecture_build_at": T4,
        "last_causal_build_at": T5,
        "last_collaboration_build_at": T6,
        "last_algorithms_run_at": T7,
        "last_embedding_at": T8,
    }


class ComputeStalenessTests(unittest.TestCase):
    def test_all_in_order_nothing_stale(self):
        result = compute_staleness(base_state())
        for stage in ("references", "knowledge", "architecture", "causal", "collaboration", "algorithms", "embeddings"):
            self.assertFalse(result[stage]["stale"], f"{stage} should not be stale")
            self.assertEqual(result[stage]["reasons"], [])

    def test_import_newer_than_extraction_makes_everything_stale(self):
        state = base_state()
        state["last_import_at"] = "2026-01-01T01:30:00+00:00"  # after references(T2), before knowledge(T3)
        result = compute_staleness(state)
        for stage in ("references", "knowledge", "architecture", "causal", "collaboration", "algorithms", "embeddings"):
            self.assertTrue(result[stage]["stale"], f"{stage} should be stale")

    def test_algorithms_stale_when_knowledge_rebuilt_after_it(self):
        state = base_state()
        state["last_algorithms_run_at"] = "2026-01-01T01:30:00+00:00"  # before knowledge(T3)
        result = compute_staleness(state)
        self.assertTrue(result["algorithms"]["stale"])
        self.assertFalse(result["references"]["stale"])
        self.assertFalse(result["knowledge"]["stale"])
        self.assertFalse(result["architecture"]["stale"])

    def test_missing_architecture_timestamp(self):
        state = base_state()
        state["last_architecture_build_at"] = None
        result = compute_staleness(state)
        self.assertTrue(result["architecture"]["stale"])
        self.assertEqual(result["architecture"]["reasons"], ["Never built."])
        for stage in ("causal", "collaboration", "algorithms", "embeddings"):
            self.assertTrue(result[stage]["stale"], f"{stage} should be stale")
        self.assertFalse(result["references"]["stale"])
        self.assertFalse(result["knowledge"]["stale"])

    def test_only_embeddings_stale_when_older_than_causal(self):
        state = base_state()
        state["last_embedding_at"] = "2026-01-01T03:30:00+00:00"  # after architecture(T4), before causal(T5)
        result = compute_staleness(state)
        self.assertTrue(result["embeddings"]["stale"])
        for stage in ("references", "knowledge", "architecture", "causal", "collaboration", "algorithms"):
            self.assertFalse(result[stage]["stale"], f"{stage} should not be stale")


if __name__ == "__main__":
    unittest.main()
