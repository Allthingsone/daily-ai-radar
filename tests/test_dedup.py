import unittest
from datetime import datetime, timezone

from daily_radar.models import RadarItem
from daily_radar.processing.dedup import cluster_news, deduplicate_exact
from daily_radar.processing.normalize import canonicalize_url, fingerprint_title


def news(title, url, source, tier):
    return RadarItem(
        kind="news",
        title=title,
        summary="An AI model release with multimodal reasoning.",
        url=url,
        canonical_url=canonicalize_url(url),
        source_id=source.lower().replace(" ", "-"),
        source_name=source,
        source_tier=tier,
        source_type="official" if tier == 1 else "media",
        source_focus=1.0,
        published_at=datetime.now(timezone.utc),
        fingerprint=fingerprint_title(title),
    )


class DedupTests(unittest.TestCase):
    def test_exact_duplicate_prefers_primary_source(self):
        media = news("New AI model", "https://example.com/a?utm_source=x", "Media", 2)
        official = news("New AI model", "https://example.com/a", "Official", 1)
        media.metadata["community_signals"] = [
            {
                "platform": "Hacker News",
                "discussion_url": "https://news.ycombinator.com/item?id=1",
                "qualified": True,
                "points": 150,
            }
        ]
        result = deduplicate_exact([media, official])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source_name, "Official")
        self.assertTrue(result[0].metadata["community_signals"][0]["qualified"])

    def test_story_cluster_preserves_alternate_sources(self):
        first = news(
            "OpenAI releases a new multimodal reasoning model",
            "https://primary.example/model",
            "Official",
            1,
        )
        second = news(
            "New multimodal reasoning model released by OpenAI",
            "https://media.example/story",
            "Media",
            2,
        )
        result = cluster_news([first, second], 0.72)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["source_count"], 2)


if __name__ == "__main__":
    unittest.main()
