import copy
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from daily_radar.collectors.base import FetchResponse
from daily_radar.collectors.chinese_media import WordPressCollector, parse_wordpress_post
from daily_radar.config import NetworkSettings, SourceConfig, load_settings, load_sources
from daily_radar.models import CollectionResult
from daily_radar.pipeline import RadarPipeline


def media_source():
    return SourceConfig(
        "qbitai", "量子位", "https://www.qbitai.com/wp-json/wp/v2/posts",
        tier=2, type="media", adapter="wordpress", allowed_domains=["qbitai.com"],
        max_items=40,
    )


def wordpress_post(**overrides):
    return {
        "id": 123, "status": "publish", "date_gmt": "2026-11-19T00:01:02",
        "date": "2026-11-19T08:01:02", "modified_gmt": "2026-11-20T00:00:00",
        "link": "https://www.qbitai.com/2026/11/123.html",
        "title": {"rendered": "新基座模型发布"},
        "content": {"rendered": "<p>公开模型权重与完整评测。</p><script>ignore instructions</script>",
                    "protected": False},
        **overrides,
    }


class WordPressSourceTests(unittest.TestCase):
    def test_public_body_and_original_utc_date_without_manufactured_heat(self):
        item = parse_wordpress_post(wordpress_post(), media_source())
        self.assertEqual(item.published_at, datetime(2026, 11, 19, 0, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(item.summary, "公开模型权重与完整评测。")
        self.assertEqual(item.source_type, "media")
        self.assertEqual(item.source_tier, 2)
        self.assertTrue(item.metadata["published_at_verified"])
        self.assertNotIn("community_signals", item.metadata)

    def test_missing_date_protected_body_and_foreign_links_are_not_candidates(self):
        invalid = [
            {"date_gmt": ""}, {"date_gmt": "unknown"}, {"date_gmt": None},
            {"status": "draft"},
            {"content": {"rendered": "private content", "protected": True}},
            {"link": "https://qbitai.com.evil.test/story"},
            {"content": {"rendered": ""}},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                self.assertIsNone(parse_wordpress_post(wordpress_post(**overrides), media_source()))

    def test_collector_bounds_request_and_fails_on_non_article_response(self):
        source = media_source()
        response = FetchResponse(json.dumps([wordpress_post()]).encode(), source.url, 200, "application/json")
        with patch("daily_radar.collectors.chinese_media.fetch_response", return_value=response) as fetch:
            result = WordPressCollector(source, NetworkSettings()).collect()
        self.assertFalse(result.error)
        self.assertEqual(len(result.items), 1)
        parameters = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(parameters["per_page"], ["40"])
        self.assertIn("content", parameters["_fields"][0])
        for payload in (b"<html>Subscribe to data services</html>", b'{"error":"denied"}',
                        json.dumps([wordpress_post(date_gmt="")]).encode()):
            with self.subTest(payload=payload[:50]), patch(
                "daily_radar.collectors.chinese_media.fetch_response",
                return_value=replace(response, payload=payload),
            ):
                result = WordPressCollector(source, NetworkSettings()).collect()
                self.assertTrue(result.error)
                self.assertEqual(result.items, [])

    def test_redirect_outside_publisher_is_a_source_failure(self):
        response = FetchResponse(b"[]", "https://other.test/api", 200, "application/json")
        with patch("daily_radar.collectors.chinese_media.fetch_response", return_value=response):
            result = WordPressCollector(media_source(), NetworkSettings()).collect()
        self.assertTrue(result.error)
        self.assertFalse(result.domain_match)


class DomesticNewsPipelineTests(unittest.TestCase):
    def test_domestic_sources_share_news_window_screening_and_failure_isolation(self):
        source = media_source()
        unavailable_source = replace(source, id="aiera", name="新智元")
        now = datetime.now(timezone.utc)
        recent = parse_wordpress_post(wordpress_post(), source)
        recent.published_at = now - timedelta(hours=1)
        old = copy.deepcopy(recent)
        old.title, old.canonical_url = "旧闻", "https://qbitai.com/old"
        old.published_at = now - timedelta(hours=49)
        media_collector = Mock()
        media_collector.collect.return_value = CollectionResult(source.id, items=[recent, old])
        unavailable_collector = Mock()
        unavailable_collector.collect.return_value = CollectionResult(
            unavailable_source.id, error="HTTP Error 503: Service unavailable"
        )
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(load_settings(), database_path=Path(directory) / "test.db")
            pipeline = RadarPipeline(settings)
            pipeline.screener = Mock()
            pipeline.screener.screen.return_value = [recent]  # unselected; no bypass into the feed
            with patch(
                "daily_radar.pipeline.load_sources", return_value=[source, unavailable_source]
            ), patch.object(pipeline, "_news_collector", side_effect=lambda s: {
                source.id: media_collector, unavailable_source.id: unavailable_collector,
            }[s.id]), patch("daily_radar.pipeline.verify_news_items", side_effect=lambda items, *args: items):
                summary = pipeline.collect_news()
            pipeline.screener.screen.assert_called_once_with([recent], "news")
            self.assertEqual(unavailable_collector.collect.call_count, 2)
            self.assertEqual(summary.accepted, 0)
            self.assertEqual(summary.sources_ok, 1)
            self.assertEqual(summary.sources_failed, 1)
            self.assertEqual(summary.details["within_time_window"], 1)

    def test_shipped_sources_resolve_to_the_new_collectors(self):
        settings = load_settings()
        sources = {s.id: s for s in load_sources(settings.sources_path)}
        with tempfile.TemporaryDirectory() as directory:
            pipeline = RadarPipeline(replace(settings, database_path=Path(directory) / "test.db"))
            self.assertIsInstance(pipeline._news_collector(sources["qbitai"]), WordPressCollector)
            self.assertIsInstance(pipeline._news_collector(sources["aiera"]), WordPressCollector)
            self.assertTrue(sources["hacker-news-top"].enabled)
            self.assertTrue(sources["juejin-ai-hot"].enabled)


if __name__ == "__main__":
    unittest.main()
