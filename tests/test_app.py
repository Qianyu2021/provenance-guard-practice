import unittest

from app import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

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

    def test_submit_requires_text_and_creator_id(self):
        response = self.client.post("/submit", json={"text": "hello"})
        self.assertEqual(response.status_code, 400)

        response = self.client.post("/submit", json={"creator_id": "creator-123"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
