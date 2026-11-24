from __future__ import annotations

import dataclasses
import unittest

from app.config import Settings
from app.sentiment import analyze_article, lexicon_analyze


def make_settings(**overrides) -> Settings:
    base = Settings.from_env()
    values = {
        "party_a_name": "Party A",
        "party_b_name": "Party B",
        "party_a_aliases": ("Party A",),
        "party_b_aliases": ("Party B",),
        "llm_provider": "none",
        "gemini_api_key": "",
    }
    values.update(overrides)
    return dataclasses.replace(base, **values)


class LexiconSentimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = make_settings()

    def analyze(self, title: str, summary: str = "") -> dict:
        return lexicon_analyze(title, summary, self.settings)

    def test_positive_news_about_party_a(self) -> None:
        result = self.analyze("Party A wins a landmark victory as supporters celebrate")
        self.assertGreater(result["A"]["score"], 0.2)
        self.assertEqual(result["A"]["method"], "lexicon")

    def test_negative_news_about_party_a(self) -> None:
        result = self.analyze("Party A faces corruption scandal as angry protests erupt")
        self.assertLess(result["A"]["score"], -0.2)

    def test_negation_flips_sentiment(self) -> None:
        result = self.analyze("Party A is not corrupt")
        self.assertGreater(result["A"]["score"], 0.0)

    def test_sentiment_is_party_specific(self) -> None:
        result = self.analyze(
            "Party A wins a huge victory while Party B suffers a damaging defeat"
        )
        self.assertGreater(result["A"]["score"], 0.1)
        self.assertLess(result["B"]["score"], -0.1)

    def test_unmentioned_party_is_neutral(self) -> None:
        result = self.analyze("Party B faces a corruption scandal")
        self.assertLess(result["B"]["score"], 0.0)
        self.assertAlmostEqual(result["A"]["score"], 0.0, places=3)

    def test_neutral_text_scores_zero(self) -> None:
        result = self.analyze("Party A and Party B held a meeting on Tuesday")
        self.assertAlmostEqual(result["A"]["score"], 0.0, places=3)
        self.assertAlmostEqual(result["B"]["score"], 0.0, places=3)

    def test_reason_is_extracted(self) -> None:
        result = self.analyze("Party A unveils a landmark welfare reform package")
        self.assertIsNotNone(result["A"]["reason"])
        self.assertIn("Party A", result["A"]["reason"])

    def test_body_sentiment_is_used(self) -> None:
        result = self.analyze(
            "Weekly briefing",
            "Party B announced a new plan. Analysts praised Party B's effective "
            "reform and described the party as credible and strong.",
        )
        self.assertGreater(result["B"]["score"], 0.1)

    def test_mention_flag_tracks_party_mentions(self) -> None:
        result = self.analyze("Party B faces a corruption scandal")
        self.assertTrue(result["B"]["mentioned"])
        self.assertFalse(result["A"]["mentioned"])

    def test_analyze_article_returns_both_parties(self) -> None:
        result = analyze_article(
            "Party A celebrates a resounding victory",
            "Party B faces a damaging defeat and angry protests.",
            self.settings,
        )
        self.assertEqual(set(result), {"A", "B"})
        self.assertGreater(result["A"]["score"], 0.0)
        self.assertLess(result["B"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
