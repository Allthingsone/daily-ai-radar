import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from daily_radar.collectors.base import FetchResponse
from daily_radar.collectors.chinese_media import WordPressCollector, parse_wordpress_post
from daily_radar.collectors.zhihu import (
    SEARCH_ENDPOINT, ZhihuSearchCollector, _NoCredentialRedirect, parse_zhihu_result,
)
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


def zhihu_source():
    return SourceConfig(
        "zhihu-ai", "知乎 · AI 讨论", SEARCH_ENDPOINT, tier=3, type="community",
        adapter="zhihu-search", allowed_domains=["zhihu.com"],
        queries=["人工智能", "大模型"], max_items=20,
        community_platform="知乎", community_min_points=100, community_min_comments=30,
    )


def zhihu_entry(**overrides):
    return {
        "Title": "如何评价新的 AI 模型？", "ContentType": "Article", "ContentID": "123",
        "Url": "https://zhuanlan.zhihu.com/p/123?utm_source=openapi",
        "ContentText": "作者讨论模型在实际工程中的表现。", "AuthorName": "测试作者",
        "CreateTime": int(datetime.now(timezone.utc).timestamp()) - 3600,
        "EditTime": int(datetime.now(timezone.utc).timestamp()),
        "VoteUpCount": 120, "CommentCount": 8, **overrides,
    }


class JSONResponse(BytesIO):
    status = 200

    def __init__(self, entries=None, payload=None):
        if payload is None:
            payload = {"Code": 0, "Data": {"Items": entries or []}}
        super().__init__(json.dumps(payload).encode())

    def geturl(self):
        return SEARCH_ENDPOINT


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


class ZhihuSourceTests(unittest.TestCase):
    def test_real_votes_and_comments_qualify_without_inventing_a_hot_rank(self):
        for counts, expected in (({"VoteUpCount": 120}, True),
                                 ({"VoteUpCount": 10, "CommentCount": 30}, True),
                                 ({"VoteUpCount": 99, "CommentCount": 29}, False)):
            with self.subTest(counts=counts):
                item = parse_zhihu_result(zhihu_entry(**counts), zhihu_source())
                signal = item.metadata["community_signals"][0]
                self.assertEqual(signal["rank"], 0)
                self.assertEqual(signal["qualified"], expected)
                self.assertEqual(item.authors, ["测试作者"])
                self.assertEqual(item.canonical_url, "https://zhuanlan.zhihu.com/p/123")
                self.assertIn("utm_source=openapi", item.url)

    def test_edit_time_or_collection_time_cannot_replace_creation_time(self):
        for created in (None, 0, "", True, "yesterday", 10**100):
            with self.subTest(created=created):
                self.assertIsNone(parse_zhihu_result(zhihu_entry(CreateTime=created), zhihu_source()))

    def test_returned_url_must_match_content_identity(self):
        for overrides in (
            {"ContentID": "456"}, {"Url": "https://zhihu.com.evil.test/p/123"},
            {"ContentType": "Article", "Url": "https://www.zhihu.com/question/123"},
            {"Url": "https://evil.test@zhuanlan.zhihu.com/p/123"},
        ):
            with self.subTest(overrides=overrides):
                self.assertIsNone(parse_zhihu_result(zhihu_entry(**overrides), zhihu_source()))
        for content_type, url in (("Question", "https://www.zhihu.com/question/123"),
                                  ("Answer", "https://www.zhihu.com/question/456/answer/123")):
            self.assertIsNotNone(parse_zhihu_result(
                zhihu_entry(ContentType=content_type, Url=url), zhihu_source()))

    def test_missing_secret_skips_network_and_requests_no_pipeline_retry(self):
        with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": ""}), patch(
            "daily_radar.collectors.zhihu.build_opener"
        ) as opener:
            result = ZhihuSearchCollector(zhihu_source(), NetworkSettings()).collect()
        opener.assert_not_called()
        self.assertEqual(result.details["status"], "not-configured")
        self.assertFalse(result.details["retryable"])
        self.assertTrue(result.error)

    def test_authenticated_search_uses_creation_window_and_deduplicates(self):
        recent = zhihu_entry()
        old = zhihu_entry(ContentID="124", Url="https://zhuanlan.zhihu.com/p/124",
                          CreateTime=int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp()))
        opener = Mock()
        opener.open.side_effect = [JSONResponse([recent, old]), JSONResponse([recent])]
        with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": "private-test-key"}), patch(
            "daily_radar.collectors.zhihu.build_opener", return_value=opener
        ):
            result = ZhihuSearchCollector(zhihu_source(), NetworkSettings()).collect()
        self.assertFalse(result.error)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.details["queries_completed"], 2)
        for call in opener.open.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer private-test-key")
            self.assertTrue(request.get_header("X-request-timestamp").isdigit())
            params = parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(params["Count"], ["10"])
            self.assertTrue(params["SortBy"][0].startswith("CreateTime:desc:("))
        self.assertNotIn("private-test-key", str(result))

    def test_credentials_cannot_be_redirected_or_sent_to_another_endpoint(self):
        request = Request(SEARCH_ENDPOINT, headers={"Authorization": "Bearer private-test-key"})
        handler = _NoCredentialRedirect()
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(request, None, code, "", {}, "https://other.test"))
        with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": "private-test-key"}), patch(
            "daily_radar.collectors.zhihu.build_opener"
        ) as opener:
            result = ZhihuSearchCollector(
                replace(zhihu_source(), url="https://other.test/search"), NetworkSettings()
            ).collect()
        self.assertTrue(result.error)
        opener.assert_not_called()

    def test_api_errors_preserve_completed_query_and_never_echo_secrets(self):
        opener = Mock()
        opener.open.side_effect = [JSONResponse([zhihu_entry()]), JSONResponse(payload={
            "Code": 30001, "Message": "private-test-key exceeded the limit",
        })]
        with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": "private-test-key"}), patch(
            "daily_radar.collectors.zhihu.build_opener", return_value=opener
        ):
            result = ZhihuSearchCollector(zhihu_source(), NetworkSettings()).collect()
        self.assertIn("30001", result.error)
        self.assertNotIn("private-test-key", str(result))
        self.assertEqual(len(result.items), 1)
        self.assertFalse(result.details["retryable"])


class DomesticNewsPipelineTests(unittest.TestCase):
    def test_domestic_sources_share_news_window_screening_and_failure_isolation(self):
        source, zhihu = media_source(), zhihu_source()
        now = datetime.now(timezone.utc)
        recent = parse_wordpress_post(wordpress_post(), source)
        recent.published_at = now - timedelta(hours=1)
        old = copy.deepcopy(recent)
        old.title, old.canonical_url = "旧闻", "https://qbitai.com/old"
        old.published_at = now - timedelta(hours=49)
        media_collector = Mock()
        media_collector.collect.return_value = CollectionResult(source.id, items=[recent, old])
        zhihu_collector = ZhihuSearchCollector(zhihu, NetworkSettings())
        zhihu_collector.collect = Mock(wraps=zhihu_collector.collect)
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(load_settings(), database_path=Path(directory) / "test.db")
            pipeline = RadarPipeline(settings)
            pipeline.screener = Mock()
            pipeline.screener.screen.return_value = [recent]  # unselected; no bypass into the feed
            with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": ""}), patch(
                "daily_radar.pipeline.load_sources", return_value=[source, zhihu]
            ), patch.object(pipeline, "_news_collector", side_effect=lambda s: {
                source.id: media_collector, zhihu.id: zhihu_collector,
            }[s.id]), patch("daily_radar.pipeline.verify_news_items", side_effect=lambda items, *args: items):
                summary = pipeline.collect_news()
            pipeline.screener.screen.assert_called_once_with([recent], "news")
            zhihu_collector.collect.assert_called_once()
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
            self.assertIsInstance(pipeline._news_collector(sources["zhihu-ai"]), ZhihuSearchCollector)
            self.assertEqual(sources["zhihu-ai"].community_rank_limit, 0)
            self.assertTrue(sources["hacker-news-top"].enabled)
            self.assertTrue(sources["juejin-ai-hot"].enabled)


if __name__ == "__main__":
    unittest.main()
