import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from daily_radar.archives import SITE_FILES, add_archive_links, build_archive, restore_published_site
from daily_radar.collectors.base import FetchResponse
from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.mailer import build_daily_message
from daily_radar.models import CollectionResult, RunSummary
from daily_radar.pipeline import RadarPipeline
from daily_radar.sample import build_demo_items
from daily_radar.static_site import build_static_site
from daily_radar.time_windows import digest_end, digest_reference
from tests.test_pipeline import FakeTwoStageScreener


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = Database(self.root / "radar.db")
        self.database.initialize()
        self.settings = load_settings()
        self.now = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)  # Shanghai Sep 26
        self.target = "2026-09-25"

    def test_date_validation_and_upper_boundary(self):
        reference = digest_reference(self.target, "Asia/Shanghai", self.now)
        self.assertEqual(reference.date().isoformat(), "2026-09-25")
        self.assertEqual(digest_end(reference, "Asia/Shanghai"), datetime(2026, 9, 25, 16, tzinfo=timezone.utc))
        self.assertEqual(digest_reference("2026-09-26", "Asia/Shanghai", self.now), self.now)
        for invalid in ("2026-09-27", "2026-9-25", "../../state", "2026-02-30"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                digest_reference(invalid, "Asia/Shanghai", self.now)

    def seed_items(self):
        news, paper = build_demo_items()[0], build_demo_items()[4]
        result = []
        for item in (news, paper):
            item.metadata["demo"] = False
            item.metadata["provenance"] = {"status": "verified-primary"}
            item.published_at = datetime(2026, 9, 25, 2, tzinfo=timezone.utc)
            item.is_important = True
            self.database.upsert_item(item)
            result.append(item)
        later = build_demo_items()[4]
        later.title = "Do not include Sep 26 in the Sep 25 replay"
        later.canonical_url += "-later"
        later.published_at = self.now
        later.metadata["demo"] = False
        later.metadata["provenance"] = {"status": "verified-primary"}
        self.database.upsert_item(later)
        return result

    def test_archive_content_day_is_not_the_build_or_usage_day(self):
        news, paper = self.seed_items()
        output = self.root / "site"
        build_static_site(self.settings, output, self.database, now=self.now, target_date=self.target)
        data = json.loads((output / "data/latest.json").read_text())
        self.assertEqual(data["digest_date"], self.target)
        self.assertEqual(data["generated_at"], self.now.isoformat())
        self.assertEqual(data["llm_usage"]["local_date"], "2026-09-26")
        self.assertEqual(data["windows"]["papers_today"]["local_date"], self.target)
        self.assertEqual([item["title"] for item in data["papers_today"]], [paper.title])
        self.assertNotIn("Do not include Sep 26", (output / "index.html").read_text())
        self.assertIn("日报补跑", (output / "index.html").read_text())

    def test_replay_mail_has_target_date_and_excludes_next_day(self):
        news, paper = self.seed_items()
        settings = replace(self.settings, email=replace(self.settings.email, username="radar@example.com"))
        message, result = build_daily_message(settings, self.database, now=self.now, target_date=self.target)
        self.assertIn("2026-09-25", message["Subject"])
        self.assertIn("补发", message["Subject"])
        self.assertEqual(result["papers"], 1)
        self.assertEqual(result["usage_date"], "2026-09-26")
        self.assertIn(paper.title, message.get_body(preferencelist=("plain",)).get_content())
        self.assertNotIn("Do not include Sep 26", message.as_string())

    def test_replay_requires_saved_news_before_collecting_papers(self):
        pipeline = RadarPipeline(self.settings, self.database)
        with patch.object(pipeline, "collect_papers") as collect:
            with self.assertRaisesRegex(ValueError, "No completed news snapshot"):
                pipeline.collect("publish", self.target)
            collect.assert_not_called()

    def test_replay_retains_original_news_lookback(self):
        news, paper = self.seed_items()
        snapshot_time = datetime(2026, 9, 24, 23, tzinfo=timezone.utc)  # Sep 25, 07:00
        news.published_at = snapshot_time - timedelta(hours=12)
        self.database.upsert_item(news)
        self.database.record_run(RunSummary(
            "news", snapshot_time, snapshot_time, 1, 1, 1, 1, 0,
            details={"prompt_version": self.settings.llm.prompt_version},
        ))
        settings = replace(self.settings, email=replace(self.settings.email, username="radar@example.com"))
        message, result = build_daily_message(settings, self.database, now=self.now, target_date=self.target)
        self.assertEqual(result["news"], 1)
        self.assertEqual(result["papers"], 1)
        self.assertIn(news.title, message.get_body(preferencelist=("plain",)).get_content())
        build_static_site(settings, self.root / "site", self.database, now=self.now, target_date=self.target)
        payload = json.loads((self.root / "site/data/latest.json").read_text())
        self.assertEqual(payload["windows"]["news"]["published_since"], (snapshot_time - timedelta(hours=settings.news.lookback_hours)).isoformat())

    def test_paper_collector_gets_target_day_but_run_time_stays_real(self):
        pipeline = RadarPipeline(self.settings, self.database)
        pipeline.screener = FakeTwoStageScreener()
        reference = digest_reference(self.target, "Asia/Shanghai", self.now)
        with patch("daily_radar.pipeline.datetime") as clock, patch("daily_radar.pipeline.ArxivCollector") as factory:
            clock.now.return_value = self.now
            collector = factory.return_value
            collector.daily_window.return_value = (reference - timedelta(days=1), reference)
            collector.collect.return_value = CollectionResult(source_id="arxiv", items=[], domain_match=True)
            summary = pipeline.collect_papers(self.target)
        collector.collect.assert_called_once_with(now=reference)
        self.assertEqual(summary.started_at, self.now)
        self.assertEqual(summary.details["digest_date"], self.target)

    def test_replay_reuses_news_and_success_on_a_later_execution_day(self):
        pipeline = RadarPipeline(self.settings, self.database)
        reference = digest_reference(self.target, "Asia/Shanghai", self.now)
        self.database.record_run(RunSummary(
            "news", reference, reference, 10, 2, 2, 1, 0,
            details={"prompt_version": self.settings.llm.prompt_version},
        ))
        summary = RunSummary("paper", self.now, self.now, 10, 1, 1, 1, 0,
                             details={"prompt_version": self.settings.llm.prompt_version, "digest_date": self.target})
        with patch.object(pipeline, "collect_papers", return_value=summary) as collect, patch.object(pipeline, "collect_news") as news:
            self.assertEqual(pipeline.collect("publish", self.target), [summary])
            collect.assert_called_once_with(self.target)
            news.assert_not_called()
            self.database.record_run(summary)
            collect.reset_mock()
            self.assertEqual(pipeline.collect("publish", self.target), [])
            collect.assert_not_called()
        self.assertFalse(pipeline._has_successful_run_today("paper", self.now))

    def test_archive_does_not_advance_the_latest_root_snapshot(self):
        self.seed_items()
        output = self.root / "site"
        old_now = datetime(2026, 9, 24, 2, tzinfo=timezone.utc)
        build_static_site(self.settings, output, self.database, now=old_now)
        old_data = (output / "data/latest.json").read_bytes()
        build_archive(self.settings, output, self.database, "https://example.com/radar/", self.target)
        self.assertEqual((output / "data/latest.json").read_bytes(), old_data)
        self.assertTrue((output / "archive/2026-09-25/data/latest.json").exists())
        self.assertIn('href="archive/2026-09-25/"', (output / "index.html").read_text())
        add_archive_links(output)
        self.assertEqual((output / "index.html").read_text().count("<!-- radar-archives:start -->"), 1)

    def test_restore_preserves_all_files_and_refuses_to_drop_unavailable_archives(self):
        requested = []
        def fetch(url, **kwargs):
            requested.append(url)
            data = json.dumps({"dates": [self.target]}).encode() if url.endswith("archive/index.json") else b"preserved"
            return FetchResponse(data, url, 200, "application/json")
        output = self.root / "restored"
        with patch("daily_radar.archives.fetch_response", side_effect=fetch):
            count = restore_published_site(self.settings, "https://example.com/radar/", output, True)
        self.assertEqual(count, 1)
        self.assertEqual(len(requested), 1 + 2 * len(SITE_FILES))
        self.assertEqual((output / "archive/2026-09-25/daily.md").read_bytes(), b"preserved")
        with patch("daily_radar.archives.fetch_response", side_effect=HTTPError("https://example.com/", 500, "unavailable", {}, None)):
            with self.assertRaises(HTTPError):
                restore_published_site(self.settings, "https://example.com/", output)

    def test_missing_archive_manifest_is_normal_on_first_deployment(self):
        output = self.root / "new-site"
        with patch("daily_radar.archives.fetch_response", side_effect=HTTPError("https://example.com/", 404, "missing", {}, None)):
            self.assertEqual(restore_published_site(self.settings, "https://example.com/", output), 0)


if __name__ == "__main__":
    unittest.main()
