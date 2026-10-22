"""AI layer tests with the network mocked (no real API calls)."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from canon import ai  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def resp(obj):
    return FakeResp(json.dumps(obj).encode())


def http_error(code, body=None):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(json.dumps(body or {}).encode()))


GOOD = {"candidates": [{"content": {"parts": [{"text": json.dumps({"findings": [
    {"quote": "double your savings", "ref": "R-10", "fix": "help you save", "severity": "high",
     "confidence": 0.9, "intent": "Show the app helps people save", "issue": "Unsupported claim"}]})}]}}]}
RULES = [{"id": "R-10", "type": "claim", "title": "Claims", "text": "Back claims with facts", "active": 1},
         {"id": "R-05", "type": "terminology", "title": "Plan name", "text": "Write Velora Pro", "active": 1}]


class GeminiTests(unittest.TestCase):
    def setUp(self):
        ai._resolved_model.clear()
        self.env = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AQ.test", "CANON_MODEL": ""}, clear=False)
        self.env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CANON_MODEL", None)

    def tearDown(self):
        self.env.stop()

    def test_provider_and_default_model(self):
        self.assertEqual(ai.provider(), "gemini")
        self.assertEqual(ai.model(), "gemini-3.1-flash-lite")
        self.assertTrue(ai.available())

    def test_review_sends_key_once_in_header(self):
        with mock.patch("urllib.request.urlopen", return_value=resp(GOOD)) as m:
            kept, dropped = ai.review("It will double your savings fast.", "calm", RULES, [], [], [])
        req = m.call_args[0][0]
        self.assertIn(":generateContent", req.full_url)
        self.assertNotIn("key=", req.full_url)  # key sent once, in the header only
        self.assertEqual(req.get_header("X-goog-api-key"), "AQ.test")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["ref"], "R-10")

    def test_retries_on_rate_limit(self):
        calls = [http_error(429), resp(GOOD)]
        with mock.patch("urllib.request.urlopen", side_effect=calls), mock.patch("time.sleep") as sl:
            kept, _ = ai.review("It will double your savings fast.", "calm", RULES, [], [], [])
        self.assertEqual(len(kept), 1)
        sl.assert_called_once()

    def test_model_not_found_falls_back_to_listed_flash(self):
        listing = {"models": [
            {"name": "models/gemini-3-flash-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-omni-1.1-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-9-flash-image", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]}
        calls = [http_error(404), resp(listing), resp(GOOD)]
        with mock.patch("urllib.request.urlopen", side_effect=calls) as m:
            kept, _ = ai.review("It will double your savings fast.", "calm", RULES, [], [], [])
        self.assertEqual(ai.model(), "gemini-3-flash-preview")  # preferred list wins
        self.assertIn("gemini-3-flash-preview:generateContent", m.call_args[0][0].full_url)
        # without preferred models, the highest stable text Flash model is chosen, never "omni"/"image"
        with mock.patch("urllib.request.urlopen", return_value=resp(listing)):
            self.assertEqual(ai._gemini_pick_model(exclude={"gemini-3-flash-preview"}), "gemini-9-flash")
        self.assertEqual(len(kept), 1)

    def test_overloaded_model_switches_to_another(self):
        listing = {"models": [
            {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3-flash-preview", "supportedGenerationMethods": ["generateContent"]}]}
        calls = [http_error(503), http_error(503), http_error(503), resp(listing), resp(GOOD)]
        with mock.patch("urllib.request.urlopen", side_effect=calls) as m, mock.patch("time.sleep"):
            kept, _ = ai.review("It will double your savings fast.", "calm", RULES, [], [], [])
        self.assertEqual(ai.model(), "gemini-3-flash-preview")
        self.assertIn("gemini-3-flash-preview:generateContent", m.call_args[0][0].full_url)
        self.assertEqual(len(kept), 1)

    def test_fallback_expires(self):
        ai._resolved_model["gemini"] = ("gemini-3-flash-preview", 0)  # already expired
        self.assertEqual(ai.model(), "gemini-3.1-flash-lite")

    def test_curly_quotes_and_spacing_still_match(self):
        text = "Act now, this offer won't last."
        found = ai.locate(text, "act now,  this offer won\u2019t last")
        self.assertEqual(found, "Act now, this offer won't last")
        self.assertIsNone(ai.locate(text, "this offer lasts forever"))

    def test_ai_cannot_cite_rule_engine_rules(self):
        bad = {"candidates": [{"content": {"parts": [{"text": json.dumps({"findings": [
            {"quote": "Plus plan", "ref": "R-05", "fix": "Velora Pro plan", "severity": "low", "confidence": 0.9}]})}]}}]}
        with mock.patch("urllib.request.urlopen", return_value=resp(bad)) as m:
            kept, dropped = ai.review("Upgrade to the Plus plan today.", "calm", RULES, [], [], [])
        self.assertEqual((len(kept), dropped), (0, 1))
        self.assertNotIn("Write Velora Pro", m.call_args[0][0].data.decode())  # rule not even shown to the AI

    def test_timeout_retried_once(self):
        with mock.patch("urllib.request.urlopen", side_effect=[TimeoutError("read timed out"), resp(GOOD)]):
            kept, _ = ai.review("It will double your savings fast.", "calm", RULES, [], [], [])
        self.assertEqual(len(kept), 1)

    def test_bad_key_gives_clear_message(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401, {"error": {"status": "UNAUTHENTICATED"}})):
            with self.assertRaises(ai.AIUnavailable) as ctx:
                ai.review("text", "calm", RULES, [], [], [])
        self.assertIn("API key rejected", str(ctx.exception))

    def test_blocked_or_empty_answer(self):
        with mock.patch("urllib.request.urlopen", return_value=resp({"promptFeedback": {"blockReason": "SAFETY"}})):
            with self.assertRaises(ai.AIUnavailable):
                ai.review("text", "calm", RULES, [], [], [])

    def test_invalid_json_retried_then_fails(self):
        bad = {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}
        with mock.patch("urllib.request.urlopen", side_effect=[resp(bad), resp(bad)]):
            with self.assertRaises(ai.AIUnavailable):
                ai.review("text", "calm", RULES, [], [], [])

    def test_confidential_terms_never_sent(self):
        with mock.patch("urllib.request.urlopen", return_value=resp({"candidates": [{"content": {"parts": [{"text": "{\"findings\": []}"}]}}]})) as m:
            ai.review("Project Nova launches at ₹599.", "calm", RULES, [], [], ["Project Nova", "₹599"])
        sent = m.call_args[0][0].data.decode()
        self.assertNotIn("Project Nova", sent)
        self.assertNotIn("599", sent)


if __name__ == "__main__":
    unittest.main()
