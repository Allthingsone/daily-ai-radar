import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.eligibility import LLM_SCREENING_RULE_VERSION
from daily_radar.models import CollectionResult, RadarItem, RunSummary
from daily_radar.pipeline import RadarPipeline


def paper(identifier, title):
    url = f"https://arxiv.org/abs/{identifier}"
    return RadarItem(
        kind="paper",
        title=title,
        summary="A public abstract supplied by the arXiv API.",
        url=url,
        canonical_url=url,
        source_id="arxiv",
        source_name="arXiv",
        source_tier=1,
        source_type="paper-api",
        source_focus=1.0,
        published_at=datetime.now(timezone.utc),
        external_id=identifier,
        metadata={
            "published_at_verified": True,
            "is_new_submission": True,
        },
    )


class FakeArxivCollector:
    items = [paper("2608.00001", "Relevant"), paper("2608.00002", "Unrelated")]

    def __init__(self, *args, **kwargs):
        pass

    def daily_window(self, now):
        return now - timedelta(days=1), now + timedelta(days=1)

    def collect(self, now=None):
        return CollectionResult(
            source_id="arxiv",
            source_name="arXiv",
            source_url="https://export.arxiv.org/api/query",
            final_url="https://export.arxiv.org/api/query",
            http_status=200,
            domain_match=True,
            items=self.items,
        )


class FailedArxivCollector(FakeArxivCollector):
    def collect(self, now=None):
        return CollectionResult(
            source_id="arxiv",
            source_name="arXiv",
            source_url="https://export.arxiv.org/api/query",
            error="RuntimeError: today's arXiv announcement is not available yet",
            domain_match=False,
        )


class FakeTwoStageScreener:
    def ensure_ready(self):
        pass

    def screen_papers_two_stage(self, items):
        values = list(items)
        for index, item in enumerate(values):
            candidate = index == 0
            item.metadata["paper_triage"] = {"candidate": candidate}
            item.metadata["llm_screening"] = {
                "selected": candidate,
                "rule_version": LLM_SCREENING_RULE_VERSION,
                "prompt_version": "2026-08-31-v3",
            }
            item.score = 90 if candidate else 0
        return values


class PipelinePaperTests(unittest.TestCase):
    def test_pipeline_uses_two_stage_screening_for_every_verified_daily_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            settings = replace(load_settings(), database_path=database.path)
            pipeline = RadarPipeline(settings, database)
            pipeline.screener = FakeTwoStageScreener()

            with patch("daily_radar.pipeline.ArxivCollector", FakeArxivCollector):
                summary = pipeline.collect_papers()

            self.assertEqual(summary.fetched, 2)
            self.assertEqual(summary.accepted, 1)
            self.assertEqual(summary.details["verified_new_submissions"], 2)
            self.assertEqual(summary.details["triage_candidates"], 1)
            self.assertEqual(summary.details["triage_rejected"], 1)
            self.assertEqual(database.stats()["papers"], 2)

    def test_all_pipeline_stops_before_news_tokens_when_arxiv_is_too_early(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            settings = replace(load_settings(), database_path=database.path)
            pipeline = RadarPipeline(settings, database)
            pipeline.screener = FakeTwoStageScreener()

            with patch(
                "daily_radar.pipeline.ArxivCollector", FailedArxivCollector
            ), patch.object(pipeline, "collect_news") as collect_news:
                summaries = pipeline.collect("all")

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].kind, "paper")
            self.assertEqual(summaries[0].sources_failed, 1)
            collect_news.assert_not_called()

    def test_publish_reuses_a_successful_news_run_from_today(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            settings = replace(load_settings(), database_path=database.path)
            pipeline = RadarPipeline(settings, database)
            now = datetime.now(timezone.utc)
            database.record_run(
                RunSummary("news", now, now, 25, 4, 4, 5, 0)
            )
            paper_summary = RunSummary("paper", now, now, 100, 2, 2, 1, 0)

            with patch.object(
                pipeline, "collect_papers", return_value=paper_summary
            ) as collect_papers, patch.object(
                pipeline, "collect_news"
            ) as collect_news:
                summaries = pipeline.collect("publish")

            self.assertEqual(summaries, [paper_summary])
            collect_papers.assert_called_once_with()
            collect_news.assert_not_called()

    def test_publish_reuses_both_stages_after_a_delivery_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            settings = replace(load_settings(), database_path=database.path)
            pipeline = RadarPipeline(settings, database)
            now = datetime.now(timezone.utc)
            database.record_run(
                RunSummary("news", now, now, 25, 4, 4, 5, 0)
            )
            database.record_run(
                RunSummary("paper", now, now, 100, 2, 2, 1, 0)
            )

            with patch.object(
                pipeline, "collect_papers"
            ) as collect_papers, patch.object(
                pipeline, "collect_news"
            ) as collect_news:
                summaries = pipeline.collect("publish")

            self.assertEqual(summaries, [])
            collect_papers.assert_not_called()
            collect_news.assert_not_called()


if __name__ == "__main__":
    unittest.main()
