from __future__ import annotations

import dataclasses
import unittest

from app.config import Settings
from app.db import to_iso, utcnow
from app.scoring import build_scored_articles, compute_scores


def make_settings(**overrides) -> Settings:
    base = Settings.from_env()
    values = {
        "party_a_name": "Party A",
        "party_b_name": "Party B",
        "party_a_aliases": ("Party A",),
        "party_b_aliases": ("Party B",),
        "source_weights": {},
        "default_source_weight": 1.0,
        "decay_lambda": 0.0,
        "sensitivity_k": 2.0,
        "cross_party_weight": 0.0,
        "window_days": 7,
        "llm_provider": "none",
        "gemini_api_key": "",
    }
    values.update(overrides)
    return dataclasses.replace(base, **values)


def make_row(
    score_a: float,
    score_b: float,
    *,
    age_hours: float = 1.0,
    domain: str = "example.com",
    title: str = "Test article",
    article_id: int = 1,
) -> dict:
    now = utcnow()
    from datetime import timedelta

    published = to_iso(now - timedelta(hours=age_hours))
    return {
        "id": article_id,
        "title": title,
        "url": f"https://{domain}/{article_id}",
        "source": "Test Source",
        "domain": domain,
        "published_at": published,
        "fetched_at": published,
        "score_a": score_a,
        "score_b": score_b,
        "reason_a": None,
        "reason_b": None,
        "method_a": "lexicon",
        "method_b": "lexicon",
    }


class ComputeScoresTests(unittest.TestCase):
    def test_no_data_is_fifty_fifty(self) -> None:
        result = compute_scores([], make_settings())
        self.assertEqual(result["p_a"], 50.0)
        self.assertEqual(result["p_b"], 50.0)
        self.assertFalse(result["has_data"])
        self.assertEqual(result["article_count"], 0)

    def test_equal_sentiment_is_fifty_fifty(self) -> None:
        settings = make_settings()
        rows = [make_row(0.4, 0.4), make_row(0.2, 0.2, article_id=2)]
        articles = build_scored_articles(rows, settings)
        result = compute_scores(articles, settings)
        self.assertAlmostEqual(result["p_a"], 50.0, places=1)
        self.assertAlmostEqual(result["p_a"] + result["p_b"], 100.0, places=1)

    def test_positive_party_a_shifts_probability(self) -> None:
        settings = make_settings()
        articles = build_scored_articles([make_row(0.6, 0.0)], settings)
        result = compute_scores(articles, settings)
        self.assertGreater(result["p_a"], 50.0)
        self.assertLess(result["p_b"], 50.0)

    def test_negative_party_b_helps_party_a(self) -> None:
        settings = make_settings()
        articles = build_scored_articles([make_row(0.0, -0.6)], settings)
        result = compute_scores(articles, settings)
        self.assertGreater(result["p_a"], 50.0)

    def test_probabilities_are_bounded(self) -> None:
        settings = make_settings(sensitivity_k=1000.0)
        articles = build_scored_articles([make_row(1.0, -1.0)], settings)
        result = compute_scores(articles, settings)
        self.assertLessEqual(result["p_a"], 99.0)
        self.assertGreaterEqual(result["p_a"], 1.0)
        self.assertAlmostEqual(result["p_a"] + result["p_b"], 100.0, places=1)

    def test_cross_party_weight_increases_advantage(self) -> None:
        rows = [make_row(0.0, -0.5)]
        base = compute_scores(
            build_scored_articles(rows, make_settings()), make_settings()
        )
        boosted_settings = make_settings(cross_party_weight=0.5)
        boosted = compute_scores(
            build_scored_articles(rows, boosted_settings), boosted_settings
        )
        self.assertGreater(boosted["delta"], base["delta"])
        self.assertGreater(boosted["p_a"], base["p_a"])

    def test_recent_articles_weigh_more(self) -> None:
        settings = make_settings(decay_lambda=0.05)
        rows = [
            make_row(0.5, 0.0, age_hours=1, article_id=1),
            make_row(-0.5, 0.0, age_hours=100, article_id=2),
        ]
        articles = build_scored_articles(rows, settings)
        self.assertGreater(articles[0]["weight"], articles[1]["weight"])
        result = compute_scores(articles, settings)
        self.assertGreater(result["p_a"], 50.0)

    def test_drivers_ranked_by_weighted_impact(self) -> None:
        settings = make_settings()
        rows = [
            make_row(0.1, 0.0, title="Small", article_id=1),
            make_row(0.9, 0.0, title="Big", article_id=2),
        ]
        result = compute_scores(build_scored_articles(rows, settings), settings)
        self.assertEqual(result["drivers"]["A"][0]["title"], "Big")
        self.assertEqual(result["drivers"]["A"][0]["impact"], "medium")

    def test_article_count(self) -> None:
        settings = make_settings()
        rows = [make_row(0.2, 0.1, article_id=index) for index in range(5)]
        result = compute_scores(build_scored_articles(rows, settings), settings)
        self.assertEqual(result["article_count"], 5)


if __name__ == "__main__":
    unittest.main()
