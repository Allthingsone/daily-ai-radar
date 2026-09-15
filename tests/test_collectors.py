import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from daily_radar.collectors.arxiv import ArxivCollector
from daily_radar.collectors.base import FetchResponse
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
    <updated>2026-08-03T02:00:00Z</updated><published>2026-08-03T01:00:00Z</published>
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
        self.assertEqual(items[0].metadata["arxiv_version_number"], 2)
        self.assertEqual(items[0].metadata["record_version_type"], "updated-version")
        self.assertEqual(items[0].metadata["updated_at"], "2026-08-03T02:00:00+00:00")

    def test_arxiv_query_is_announcement_wide_not_keyword_filtered(self):
        fixed = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
        collector = ArxivCollector(
            PaperSettings(), NetworkSettings(), clock=lambda: fixed
        )
        url = collector.build_url()
        query = parse_qs(urlsplit(url).query)
        search = query["search_query"][0]
        self.assertIn("cat:cs.CV", search)
        self.assertIn("submittedDate:[202607301800 TO 202607311759]", search)
        self.assertNotIn("autonomous driving", search)
        self.assertNotIn("vision-language", search)
        self.assertEqual(query["start"], ["0"])
        self.assertEqual(query["max_results"], ["200"])

    def test_arxiv_collector_pages_to_total_and_marks_daily_new_submissions(self):
        fixed = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)

        def page(identifier, title, published):
            return f"""<?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom"
                  xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
              <opensearch:totalResults>2</opensearch:totalResults>
              <entry>
                <id>http://arxiv.org/abs/{identifier}v1</id>
                <updated>{published}</updated><published>{published}</published>
                <title>{title}</title><summary>Daily abstract.</summary>
                <author><name>A. Author</name></author><category term="cs.AI"/>
              </entry>
            </feed>""".encode("utf-8")

        payloads = {
            0: page("2608.00001", "First daily paper", "2026-07-31T17:00:00Z"),
            1: page("2608.00002", "Second daily paper", "2026-07-31T16:30:00Z"),
        }
        starts = []

        def fake_fetch(url, **kwargs):
            start = int(parse_qs(urlsplit(url).query)["start"][0])
            starts.append(start)
            return FetchResponse(
                payload=payloads[start],
                final_url=url,
                status=200,
                content_type="application/atom+xml",
            )

        sleeps = []
        collector = ArxivCollector(
            PaperSettings(page_size=1, page_delay_seconds=3.0),
            NetworkSettings(),
            clock=lambda: fixed,
            sleeper=sleeps.append,
        )
        with patch("daily_radar.collectors.arxiv.fetch_response", fake_fetch):
            result = collector.collect()

        self.assertEqual(result.error, "")
        self.assertEqual(starts, [0, 1])
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(len(result.items), 2)
        self.assertTrue(all(item.metadata["is_new_submission"] for item in result.items))
        self.assertTrue(all(item.metadata["submission_type"] == "new-submission" for item in result.items))
        self.assertTrue(
            all(
                item.published_at.isoformat() == "2026-08-03T00:00:00+00:00"
                for item in result.items
            )
        )
        self.assertEqual(
            result.items[0].metadata["arxiv_first_submitted_at"],
            "2026-07-31T17:00:00+00:00",
        )

    def test_arxiv_announcement_window_follows_eastern_schedule(self):
        summer = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
        collector = ArxivCollector(PaperSettings(), NetworkSettings())
        submitted_since, submitted_before, announced_at = (
            collector.announcement_window(summer)
        )
        self.assertEqual(submitted_since.isoformat(), "2026-08-27T18:00:00+00:00")
        self.assertEqual(submitted_before.isoformat(), "2026-08-28T18:00:00+00:00")
        self.assertEqual(announced_at.isoformat(), "2026-08-31T00:00:00+00:00")

        winter = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
        submitted_since, submitted_before, announced_at = (
            collector.announcement_window(winter)
        )
        self.assertEqual(submitted_since.isoformat(), "2026-01-01T19:00:00+00:00")
        self.assertEqual(submitted_before.isoformat(), "2026-01-02T19:00:00+00:00")
        self.assertEqual(announced_at.isoformat(), "2026-01-05T01:00:00+00:00")

    def test_incomplete_or_inconsistent_api_pagination_never_returns_partial_items(self):
        fixed = datetime(2026, 8, 4, 2, 0, tzinfo=timezone.utc)
        first_page = ARXIV.replace(b"  <entry>", b"  <totalResults>2</totalResults><entry>")
        for second_page in (
            b"<feed><totalResults>2</totalResults></feed>",
            first_page,
            first_page.replace(b"<totalResults>2", b"<totalResults>3"),
            first_page.replace(b"<published>2026-08-03T01:00:00Z", b"<published>invalid-date"),
        ):
            pages = iter([first_page, second_page])
            collector = ArxivCollector(PaperSettings(listing_fallback_enabled=False),
                                       NetworkSettings(), sleeper=lambda _: None)
            with self.subTest(second_page=second_page[:80]), patch(
                "daily_radar.collectors.arxiv.fetch_response",
                side_effect=lambda url, **kwargs: FetchResponse(next(pages), url, 200, "application/atom+xml"),
            ):
                result = collector.collect(fixed)
            self.assertTrue(result.error)
            self.assertEqual(result.items, [])

    def test_weekday_before_eastern_announcement_fails_without_stale_batch(self):
        before_release = datetime(2026, 8, 30, 23, 30, tzinfo=timezone.utc)
        collector = ArxivCollector(
            PaperSettings(), NetworkSettings(), clock=lambda: before_release
        )
        with patch("daily_radar.collectors.arxiv.fetch_response") as fetch:
            result = collector.collect()
        self.assertIn("not available yet", result.error)
        fetch.assert_not_called()

    def test_empty_expected_announcement_fails_for_backup_retry(self):
        after_release = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)
        empty = b"""<?xml version="1.0"?><feed
        xmlns="http://www.w3.org/2005/Atom"
        xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>0</opensearch:totalResults></feed>"""
        collector = ArxivCollector(
            PaperSettings(listing_fallback_enabled=False), NetworkSettings(), clock=lambda: after_release
        )
        response = FetchResponse(
            payload=empty,
            final_url="https://export.arxiv.org/api/query",
            status=200,
            content_type="application/atom+xml",
        )
        with patch(
            "daily_radar.collectors.arxiv.fetch_response", return_value=response
        ):
            result = collector.collect()
        self.assertIn("search index may not be ready", result.error)

    def test_weekend_without_new_announcement_is_a_valid_empty_day(self):
        saturday = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)
        collector = ArxivCollector(
            PaperSettings(), NetworkSettings(), clock=lambda: saturday
        )
        with patch("daily_radar.collectors.arxiv.fetch_response") as fetch:
            result = collector.collect()
        self.assertEqual(result.error, "")
        self.assertEqual(result.items, [])
        fetch.assert_not_called()

    def test_confirmed_deferred_mailing_is_a_valid_empty_shanghai_day(self):
        papers = PaperSettings(deferred_announcement_dates=["2026-09-07"])
        for fixed in (
            datetime(2026, 9, 7, 23, 30, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc),
        ):
            with self.subTest(now=fixed):
                collector = ArxivCollector(papers, NetworkSettings())
                with patch("daily_radar.collectors.arxiv.fetch_response") as fetch:
                    result = collector.collect(now=fixed)
                self.assertEqual(result.error, "")
                self.assertEqual(result.items, [])
                self.assertEqual(
                    result.details["announcement_status"], "not_scheduled"
                )
                self.assertEqual(result.http_status, 0)
                fetch.assert_not_called()

    def test_resumed_mailing_includes_all_deferred_submission_days(self):
        # The official Labor Day notice merges Friday 14:00 through Tuesday
        # 14:00 Eastern into Tuesday's 20:00 mailing (Wednesday in Shanghai).
        fixed = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)
        papers = PaperSettings(deferred_announcement_dates=["2026-09-07"])
        collector = ArxivCollector(papers, NetworkSettings())
        submissions = (
            "2026-09-04T19:00:00Z",
            "2026-09-07T15:00:00Z",
            "2026-09-08T17:59:00Z",
        )
        entries = "".join(
            f"""<entry>
              <id>http://arxiv.org/abs/2609.{index:05d}v1</id>
              <published>{submitted}</published><updated>{submitted}</updated>
              <title>Deferred paper {index}</title><summary>Public abstract.</summary>
              <author><name>A. Author</name></author><category term="cs.AI"/>
            </entry>"""
            for index, submitted in enumerate(submissions, start=1)
        )
        payload = f"""<feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
          <opensearch:totalResults>3</opensearch:totalResults>{entries}</feed>"""
        response = FetchResponse(
            payload=payload.encode("utf-8"),
            final_url="https://export.arxiv.org/api/query",
            status=200,
            content_type="application/atom+xml",
        )
        with patch(
            "daily_radar.collectors.arxiv.fetch_response", return_value=response
        ) as fetch:
            result = collector.collect(now=fixed)
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.items), 3)
        search = parse_qs(urlsplit(fetch.call_args.args[0]).query)["search_query"][0]
        self.assertIn("submittedDate:[202609041800 TO 202609081759]", search)
        self.assertEqual(
            [item.metadata["arxiv_first_submitted_at"] for item in result.items],
            [value.replace("Z", "+00:00") for value in submissions],
        )
        self.assertTrue(all(
            item.published_at == datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
            for item in result.items
        ))

    def test_resumed_mailing_still_waits_for_its_release_time(self):
        papers = PaperSettings(deferred_announcement_dates=["2026-09-07"])
        collector = ArxivCollector(papers, NetworkSettings())
        before_release = datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc)
        with patch("daily_radar.collectors.arxiv.fetch_response") as fetch:
            result = collector.collect(now=before_release)
        self.assertIn("not available yet", result.error)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
