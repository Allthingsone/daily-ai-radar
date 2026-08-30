import unittest

from daily_radar.collectors.arxiv import ArxivCollector
from daily_radar.collectors.community import (
    parse_csdn_article_metadata,
    parse_hackernews_story,
    parse_juejin_ranked_article,
)
from daily_radar.collectors.rss import parse_feed
from daily_radar.config import NetworkSettings, PaperSettings, SourceConfig


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title><item>
  <title>New AI Model</title>
  <link>https://example.com/model?utm_source=feed</link>
  <guid>story-1</guid>
  <pubDate>Mon, 03 Aug 2026 02:00:00 GMT</pubDate>
  <description><![CDATA[<p>A multimodal model release.</p>]]></description>
</item></channel></rss>"""

ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.01234v2</id>
    <updated>2026-08-03T01:00:00Z</updated><published>2026-08-03T01:00:00Z</published>
    <title>DriveVLA for Autonomous Driving</title>
    <summary>A vision-language-action driving agent.</summary>
    <author><name>A. Researcher</name></author>
    <category term="cs.CV"/>
    <link title="pdf" href="https://arxiv.org/pdf/2608.01234v2" type="application/pdf"/>
    <arxiv:comment>Code: https://github.com/example/drivevla</arxiv:comment>
  </entry>
</feed>"""


class CollectorParserTests(unittest.TestCase):
    def test_hacker_news_story_keeps_verifiable_discussion_metrics(self):
        source = SourceConfig(
            id="hn",
            name="Hacker News",
            url="https://hacker-news.firebaseio.com/v0/topstories.json",
            tier=3,
            type="community",
            community_platform="Hacker News",
            community_rank_limit=30,
            community_min_points=100,
            community_min_comments=30,
        )
        item = parse_hackernews_story(
            {
                "id": 123,
                "type": "story",
                "time": 1785722400,
                "title": "A multimodal reasoning model is released",
                "url": "https://example.com/model",
                "by": "researcher",
                "score": 120,
                "descendants": 42,
            },
            source,
            rank=4,
        )
        self.assertIsNotNone(item)
        signal = item.metadata["community_signals"][0]
        self.assertTrue(signal["qualified"])
        self.assertEqual(signal["points"], 120)
        self.assertEqual(signal["comments"], 42)
        self.assertEqual(
            signal["discussion_url"], "https://news.ycombinator.com/item?id=123"
        )

    def test_csdn_article_metadata_uses_the_original_publication_time(self):
        payload = b"""<html><head>
        <meta property="article:published_time" content="2026-08-28T21:12:43+08:00">
        <meta property="og:description" content="A public technical summary.">
        </head></html>"""
        metadata = parse_csdn_article_metadata(payload)
        self.assertEqual(
            metadata["published_at"], "2026-08-28T21:12:43+08:00"
        )
        self.assertEqual(metadata["description"], "A public technical summary.")

    def test_juejin_item_uses_schema_date_and_ranked_engagement(self):
        source = SourceConfig(
            id="juejin-ai",
            name="掘金 AI 热榜",
            url="https://api.juejin.cn/content_api/v1/content/article_rank",
            tier=3,
            type="community",
            community_platform="掘金",
            community_rank_limit=20,
            community_min_points=300,
            community_min_comments=10,
            allowed_domains=["api.juejin.cn", "juejin.cn"],
        )
        entry = {
            "content": {
                "content_id": "7678531174247874586",
                "title": "GLM foundation model hands-on discussion",
            },
            "content_counter": {
                "view": 891,
                "like": 9,
                "collect": 6,
                "hot_rank": 495,
                "comment_count": 2,
            },
            "author": {"name": "tester"},
        }
        article = b"""<html><head><script type="application/ld+json">
        [{"@context":"https://schema.org","@type":"BlogPosting",
        "headline":"GLM foundation model hands-on discussion",
        "description":"A Chinese developer community discussion.",
        "author":{"@type":"Organization","name":"tester"},
        "datePublished":"2026-08-27T11:15:21+00:00"}]
        </script></head></html>"""
        item = parse_juejin_ranked_article(
            entry,
            article,
            "https://juejin.cn/post/7678531174247874586",
            source,
            rank=7,
        )
        self.assertIsNotNone(item)
        self.assertEqual(item.published_at.isoformat(), "2026-08-27T11:15:21+00:00")
        signal = item.metadata["community_signals"][0]
        self.assertTrue(signal["qualified"])
        self.assertEqual(signal["rank"], 7)
        self.assertEqual(signal["points"], 495)
        self.assertEqual(signal["views"], 891)
        self.assertEqual(signal["likes"], 9)
        self.assertEqual(signal["favorites"], 6)
        self.assertIsNone(
            parse_juejin_ranked_article(
                entry,
                article,
                "https://juejin.cn/post/a-different-id",
                source,
                rank=7,
            )
        )

    def test_rss_parser(self):
        source = SourceConfig(
            id="test", name="Test", url="https://example.com/feed", tier=1
        )
        items = parse_feed(RSS, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].canonical_url, "https://example.com/model")
        self.assertEqual(items[0].summary, "A multimodal model release.")

    def test_arxiv_parser(self):
        items = ArxivCollector.parse(ARXIV)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].external_id, "2608.01234")
        self.assertEqual(items[0].metadata["code_url"], "https://github.com/example/drivevla")
        self.assertEqual(items[0].authors, ["A. Researcher"])

    def test_arxiv_query_is_domain_targeted(self):
        collector = ArxivCollector(PaperSettings(), NetworkSettings())
        url = collector.build_url()
        self.assertIn("autonomous+driving", url)
        self.assertIn("vision-language", url)
        self.assertIn("cat%3Acs.CV", url)


if __name__ == "__main__":
    unittest.main()
