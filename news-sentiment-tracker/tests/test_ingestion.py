from __future__ import annotations

import unittest

from app.ingestion import (
    alias_pattern,
    canonical_url,
    domain_of,
    matches_any,
    normalize_title,
    strip_html,
)


class IngestionHelpersTests(unittest.TestCase):
    def test_canonical_url_strips_tracking_and_www(self) -> None:
        url = "https://www.Example.com/news/story/?utm_source=x&utm_medium=y&id=42#top"
        self.assertEqual(canonical_url(url), "https://example.com/news/story?id=42")

    def test_canonical_url_strips_trailing_slash(self) -> None:
        self.assertEqual(
            canonical_url("https://example.com/path/"), "https://example.com/path"
        )

    def test_domain_of_strips_www(self) -> None:
        self.assertEqual(domain_of("https://www.reuters.com/world"), "reuters.com")

    def test_normalize_title(self) -> None:
        self.assertEqual(
            normalize_title("Party A: Wins! (Again)"), "party a wins again"
        )

    def test_strip_html(self) -> None:
        self.assertEqual(
            strip_html("<p>Hello &amp; <b>world</b></p>"), "Hello & world"
        )

    def test_matches_any_respects_word_boundaries(self) -> None:
        aliases = ("Party A",)
        self.assertTrue(matches_any("News about Party A today", aliases))
        self.assertFalse(matches_any("News about Party Awesome today", aliases))

    def test_proper_noun_aliases_are_case_sensitive(self) -> None:
        aliases = ("Party A",)
        self.assertTrue(matches_any("Party A wins the vote", aliases))
        self.assertFalse(matches_any("They joined party a yesterday", aliases))

    def test_lowercase_aliases_are_case_insensitive(self) -> None:
        self.assertTrue(matches_any("BJP wins the vote", ("bjp",)))

    def test_alias_pattern_is_case_aware(self) -> None:
        self.assertTrue(alias_pattern(("Party A",)).search("Party A wins"))
        self.assertFalse(alias_pattern(("Party A",)).search("party a wins"))


if __name__ == "__main__":
    unittest.main()
