import os
import tempfile
import unittest

# Point the audit log at a throwaway DB before app/audit_log import it.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["AUDIT_LOG_DB"] = _tmp_db.name

import audit_log
from app import app
from signals import (
    AI_THRESHOLD,
    HUMAN_THRESHOLD,
    analyze_with_groq,
    classify,
    combine_signals,
    compute_burstiness_signal,
)

HUMAN_BURSTY = (
    "We got lost. Twice, actually, before we even found the trailhead, and by "
    "then the fog had rolled in so thick that the guidebook's cheerful promise of "
    "'panoramic ridge views' felt like a cruel joke.\n\n"
    "Still. We climbed. My boots were soaked, my friend was singing something "
    "off-key, and somewhere around the third switchback the clouds just tore "
    "open. The whole valley, right there. Worth it."
)
AI_UNIFORM = (
    "The city offers a wide range of attractions for every type of traveler. "
    "Visitors can explore the historic district and admire the local "
    "architecture. There are many restaurants that serve delicious traditional "
    "cuisine. The museums provide fascinating insights into the region's rich "
    "history. Travelers should also consider visiting the beautiful public "
    "gardens nearby. Overall, the destination promises a memorable experience."
)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_first_signal_returns_structured_assessment(self):
        result = analyze_with_groq("This is a short human-written travel reflection.")

        self.assertIn("score", result)
        self.assertIn("result", result)
        self.assertIn("rationale", result)
        self.assertIn(result["result"], {"likely_ai", "likely_human", "uncertain"})
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_submit_returns_expected_structure(self):
        response = self.client.post(
            "/submit",
            json={
                "text": "This is a short human-written travel reflection.",
                "creator_id": "creator-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("content_id", body)
        self.assertIn("result", body)
        self.assertIn("confidence_score", body)
        self.assertIn("label_text", body)
        self.assertIn("signals", body)
        self.assertEqual(body["submitted_by"], "creator-123")
        self.assertIsInstance(body["confidence_score"], (int, float))
        self.assertTrue(body["content_id"])

    def test_submit_requires_text_and_creator_id(self):
        response = self.client.post("/submit", json={"text": "hello"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/submit", json={"creator_id": "creator-123"})
        self.assertEqual(response.status_code, 400)

    def test_submit_writes_structured_audit_entry(self):
        response = self.client.post(
            "/submit",
            json={
                "text": "This is a short human-written travel reflection.",
                "creator_id": "creator-abc",
            },
        )
        content_id = response.get_json()["content_id"]

        with audit_log._connect() as conn:
            row = conn.execute(
                "SELECT * FROM audit_log WHERE content_id = ?", (content_id,)
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["creator_id"], "creator-abc")
        self.assertEqual(row["status"], "classified")
        self.assertIn(row["attribution"], {"likely_ai", "likely_human", "uncertain"})
        self.assertIsNotNone(row["timestamp"])
        self.assertGreaterEqual(row["confidence"], 0.0)
        self.assertLessEqual(row["confidence"], 1.0)
        self.assertGreaterEqual(row["llm_score"], 0.0)
        self.assertLessEqual(row["llm_score"], 1.0)

    def test_log_endpoint_returns_recent_entries_newest_first(self):
        first = self.client.post(
            "/submit",
            json={"text": "First travel note about the coast.", "creator_id": "u1"},
        ).get_json()["content_id"]
        second = self.client.post(
            "/submit",
            json={"text": "Second travel note about the mountains.", "creator_id": "u2"},
        ).get_json()["content_id"]

        response = self.client.get("/log")
        self.assertEqual(response.status_code, 200)
        entries = response.get_json()["entries"]

        self.assertIsInstance(entries, list)
        content_ids = [e["content_id"] for e in entries]
        self.assertIn(first, content_ids)
        self.assertIn(second, content_ids)
        # Newest first: the second submission appears before the first.
        self.assertLess(content_ids.index(second), content_ids.index(first))
        for key in ("content_id", "creator_id", "timestamp", "attribution",
                    "confidence", "llm_score", "status"):
            self.assertIn(key, entries[0])

    def test_400_requests_are_not_logged(self):
        with audit_log._connect() as conn:
            before = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

        self.client.post("/submit", json={"text": "hello"})

        with audit_log._connect() as conn:
            after = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

        self.assertEqual(before, after)


class BurstinessSignalTests(unittest.TestCase):
    """Signal 2 tested independently, before integration."""

    def test_score_is_normalized(self):
        for text in (HUMAN_BURSTY, AI_UNIFORM, "", "One sentence only."):
            score = compute_burstiness_signal(text)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_short_text_is_neutral(self):
        # < 2 sentences => no structural information => neutral midpoint.
        self.assertEqual(compute_burstiness_signal("Just one sentence."), 0.5)

    def test_bursty_human_scores_lower_than_uniform_ai(self):
        human = compute_burstiness_signal(HUMAN_BURSTY)
        ai = compute_burstiness_signal(AI_UNIFORM)
        self.assertLess(human, ai)
        self.assertLess(human, 0.5)   # leans human
        self.assertGreater(ai, 0.5)   # leans AI


class ConfidenceScorerTests(unittest.TestCase):
    """Scorer must implement the planning.md thresholds exactly."""

    def test_classify_thresholds_match_spec(self):
        self.assertEqual(classify(AI_THRESHOLD), "likely_ai")
        self.assertEqual(classify(AI_THRESHOLD - 0.001), "uncertain")
        self.assertEqual(classify(HUMAN_THRESHOLD), "uncertain")
        self.assertEqual(classify(HUMAN_THRESHOLD - 0.001), "likely_human")
        self.assertEqual(classify(0.0), "likely_human")
        self.assertEqual(classify(1.0), "likely_ai")

    def test_both_signals_agree(self):
        self.assertEqual(combine_signals(0.90, 0.85)["result"], "likely_ai")
        self.assertEqual(combine_signals(0.10, 0.12)["result"], "likely_human")

    def test_disagreement_pulls_toward_uncertain(self):
        out = combine_signals(0.95, 0.10)
        self.assertEqual(out["result"], "uncertain")
        self.assertGreater(out["disagreement"], 0.5)

    def test_no_single_signal_yields_confident_ai(self):
        # Signal 1 maxed, signal 2 below the corroboration floor => never AI.
        for s2 in (0.0, 0.3, 0.55, 0.59):
            out = combine_signals(1.0, s2)
            self.assertNotEqual(out["result"], "likely_ai")
            self.assertLess(out["score"], AI_THRESHOLD)
            self.assertFalse(out["corroborated"])
        # Once both corroborate, an AI verdict becomes reachable.
        self.assertEqual(combine_signals(1.0, 0.90)["result"], "likely_ai")


if __name__ == "__main__":
    unittest.main()
