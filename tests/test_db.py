import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_radar.db import Database
from daily_radar.eligibility import NEWS_GATE_RULE_VERSION
from daily_radar.models import CollectionResult, RunSummary
from daily_radar.sample import build_demo_items


class DatabaseTests(unittest.TestCase):
    def test_upsert_query_and_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            item = build_demo_items()[0]
            item_id = database.upsert_item(item)
            item.score += 1
            second_id = database.upsert_item(item)
            self.assertEqual(item_id, second_id)
            self.assertEqual(database.stats()["total"], 1)

            database.record_feedback(item_id, "saved")
            stored = database.list_items(kind="news")
            self.assertEqual(stored[0]["feedback_value"], "saved")
            self.assertIsInstance(stored[0]["reasons"], list)

    def test_demo_records_can_be_purged_without_touching_real_items(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            demo, real = build_demo_items()[:2]
            real.metadata["demo"] = False
            real.canonical_url += "-real"
            real.url += "-real"
            database.upsert_item(demo)
            database.upsert_item(real)
            self.assertEqual(database.purge_demo(), 1)
            self.assertEqual(database.stats()["total"], 1)

    def test_source_health_records_keep_domain_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            now = datetime.now(timezone.utc)
            run_id = database.record_run(
                RunSummary("news", now, now, 3, 1, 1, 1, 0)
            )
            database.record_source_checks(
                run_id,
                [
                    CollectionResult(
                        source_id="official",
                        source_name="Official",
                        source_url="https://example.com/feed",
                        final_url="https://example.com/feed",
                        http_status=200,
                        domain_match=True,
                    )
                ],
            )
            check = database.recent_source_checks()[0]
            self.assertTrue(check["success"])
            self.assertEqual(check["domain_match"], 1)

    def test_publication_cutoff_excludes_older_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            newest, older = build_demo_items()[:2]
            now = datetime.now(timezone.utc)
            newest.published_at = now
            older.published_at = now - timedelta(days=5)
            database.upsert_item(newest)
            database.upsert_item(older)

            cutoff = now - timedelta(days=1)
            stored = database.list_items(kind="news", published_since=cutoff)
            self.assertEqual([item["title"] for item in stored], [newest.title])
            self.assertEqual(database.stats(published_since=cutoff)["total"], 1)

    def test_feed_eligibility_hides_news_without_release_or_result_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            accepted, rejected = build_demo_items()[:2]
            accepted.metadata["news_gate"] = {
                "passed": True,
                "rule_version": NEWS_GATE_RULE_VERSION,
            }
            rejected.metadata["news_gate"] = {
                "passed": False,
                "rule_version": NEWS_GATE_RULE_VERSION,
            }
            database.upsert_item(accepted)
            database.upsert_item(rejected)

            stored = database.list_items(kind="news", eligible_only=True)
            self.assertEqual([item["title"] for item in stored], [accepted.title])
            self.assertEqual(database.stats(eligible_only=True)["news"], 1)


if __name__ == "__main__":
    unittest.main()
