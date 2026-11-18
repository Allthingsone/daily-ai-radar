import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.exporter import export_all
from daily_radar.mailer import build_daily_message
from daily_radar.models import CollectionResult, RunSummary
from daily_radar.pipeline import RadarPipeline
from daily_radar.sample import build_demo_items
from daily_radar.static_site import build_static_site
from daily_radar.time_windows import PAPER_COLLECTION_VERSION, build_period_window


class PreviousDayDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = Database(self.root / "radar.db")
        self.db.initialize()
        settings = load_settings()
        self.settings = replace(settings, database_path=self.db.path,
                                email=replace(settings.email, username="radar@example.com"))
        self.now = datetime(2026, 11, 17, 23, tzinfo=timezone.utc)  # Nov 18, 07:00 Shanghai
        self.paper_time = datetime(2026, 11, 17, 1, tzinfo=timezone.utc)

    def seed(self, item, when, title=None):
        item = deepcopy(item)
        item.published_at = when
        if title:
            item.title = title
            item.canonical_url += "-" + title
        item.metadata.update({"demo": False, "provenance": {"status": "verified-primary"},
                              "paper_collection_version": PAPER_COLLECTION_VERSION})
        item.is_important = True
        self.db.upsert_item(item)
        return item

    def summary(self, status="available", **details):
        return RunSummary("paper", self.now, self.now, 1, 1, 1, 1, 0, details={
            "digest_date": "2026-11-18", "paper_date": "2026-11-17", "paper_status": status,
            "paper_collection_version": PAPER_COLLECTION_VERSION,
            "prompt_version": self.settings.llm.prompt_version, **details,
        })

    def test_news_stays_current_and_papers_exclude_next_day_and_legacy_membership(self):
        news = self.seed(build_demo_items()[0], self.now)
        paper = self.seed(build_demo_items()[4], self.paper_time, "expected-batch")
        self.seed(paper, self.now, "today-paper")
        legacy = self.seed(paper, self.paper_time, "legacy-guessed-batch")
        legacy.metadata.pop("paper_collection_version")
        self.db.upsert_item(legacy)
        self.db.record_run(self.summary())
        output = self.root / "site"
        build_static_site(self.settings, output, self.db, now=self.now)
        data = json.loads((output / "data/latest.json").read_text())
        self.assertEqual(data["digest_date"], "2026-11-18")
        self.assertEqual(data["paper_date"], "2026-11-17")
        self.assertEqual(data["llm_usage"]["local_date"], "2026-11-18")
        self.assertEqual([item["title"] for item in data["news"]], [news.title])
        self.assertEqual([item["title"] for item in data["papers_today"]], [paper.title])
        message, result = build_daily_message(self.settings, self.db, now=self.now)
        self.assertIn("2026-11-18", message["Subject"])
        self.assertEqual((result["news"], result["papers"]), (1, 1))
        for kind in ("html", "plain"):
            body = message.get_body(preferencelist=(kind,)).get_content()
            self.assertIn("公告日期：2026-11-17", body)
            self.assertIn(news.title, body)
            self.assertIn(paper.title, body)
            self.assertNotIn("today-paper", body)
            self.assertNotIn("legacy-guessed-batch", body)

    def test_empty_batch_states_are_explained_without_hiding_unavailable_sources(self):
        self.seed(build_demo_items()[0], self.now)
        for status, expected in (("not_scheduled", "目标日期无新公告"),
                                 ("no_new_submissions", "目标日期没有新增论文"),
                                 ("no_matches", "没有符合关注方向的论文"),
                                 ("unavailable", "不能判定为没有论文")):
            with self.subTest(status=status):
                self.db.record_run(self.summary(status))
                message, result = build_daily_message(self.settings, self.db, now=self.now)
                self.assertEqual((result["news"], result["papers"]), (1, 0))
                self.assertIn(expected, message.get_body(preferencelist=("plain",)).get_content())
                build_static_site(self.settings, self.root / "site", self.db, now=self.now)
                self.assertIn(expected, (self.root / "site/index.html").read_text())

    def test_legacy_or_wrong_batch_success_does_not_suppress_new_collection(self):
        pipeline = RadarPipeline(self.settings, self.db)
        for details in ({"paper_date": "2026-11-18"}, {"paper_collection_version": ""},
                        {"paper_collection_version": "submission-window"}):
            self.db.record_run(self.summary(**details))
            self.assertFalse(pipeline._has_successful_run_today("paper", self.now))
        self.db.record_run(self.summary())
        self.assertTrue(pipeline._has_successful_run_today("paper", self.now))

    def test_invalid_records_fail_instead_of_becoming_no_matches_or_using_model_tokens(self):
        pipeline = RadarPipeline(self.settings, self.db)
        pipeline.screener = Mock()
        paper = build_demo_items()[4]
        paper.published_at = self.paper_time
        paper.metadata["is_new_submission"] = False
        with patch("daily_radar.pipeline.datetime") as clock, patch("daily_radar.pipeline.ArxivCollector") as factory:
            clock.now.return_value = self.now
            collector = factory.return_value
            collector.daily_window.return_value = (self.paper_time - timedelta(hours=9), self.paper_time + timedelta(hours=15))
            collector.collect.return_value = CollectionResult(source_id="arxiv", items=[paper])
            summary = pipeline.collect_papers()
        self.assertEqual(summary.sources_failed, 1)
        self.assertEqual(summary.details["paper_status"], "unavailable")
        pipeline.screener.ensure_ready.assert_not_called()
        pipeline.screener.screen_papers_two_stage.assert_not_called()

    def test_export_respects_the_paper_upper_bound_without_shifting_news(self):
        news = self.seed(build_demo_items()[0], self.now)
        paper = self.seed(build_demo_items()[4], self.paper_time, "expected-batch")
        self.seed(paper, self.now, "today-paper")
        window = build_period_window("paper", "today", "Asia/Shanghai", 96, self.now, paper_lag_days=1)
        paths = export_all(self.db, self.root / "export", {"news": self.now - timedelta(hours=48), "paper": window.published_since},
                           prompt_version=self.settings.llm.prompt_version,
                           published_before_by_kind={"paper": window.published_before},
                           paper_collection_version=PAPER_COLLECTION_VERSION)
        markdown = paths[1].read_text()
        self.assertIn(paper.title, markdown)
        self.assertIn(news.title, markdown)
        self.assertNotIn("today-paper", markdown)


if __name__ == "__main__":
    unittest.main()
