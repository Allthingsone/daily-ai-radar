import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.eligibility import LLM_SCREENING_RULE_VERSION
from daily_radar.sample import build_demo_items
from daily_radar.static_site import build_static_site


class StaticSiteTests(unittest.TestCase):
    def test_builds_pages_snapshot_with_strict_today_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "radar.db")
            database.initialize()
            fixed_now = datetime(2026, 8, 29, 1, 30, tzinfo=timezone.utc)

            demo_items = build_demo_items()
            news = demo_items[0]
            paper_today = demo_items[4]
            paper_old = deepcopy(demo_items[5])
            unverified = demo_items[1]

            for item in (news, paper_today, paper_old, unverified):
                item.metadata["demo"] = False
            news.published_at = fixed_now - timedelta(hours=2)
            news.metadata["provenance"] = {
                "status": "verified-primary",
                "domain": "example.com",
                "http_status": 200,
                "method": "source-domain-match",
            }
            news.metadata["llm_screening"] = {
                "selected": True,
                "rule_version": LLM_SCREENING_RULE_VERSION,
                "prompt_version": "2026-08-30-v2",
            }
            paper_today.published_at = fixed_now - timedelta(hours=1)
            paper_today.metadata["provenance"] = {
                "status": "verified-primary",
                "domain": "arxiv.org",
                "http_status": 200,
                "method": "arxiv-id-match",
            }
            paper_old.published_at = fixed_now - timedelta(days=2)
            paper_old.metadata["provenance"] = {
                "status": "verified-primary",
                "domain": "arxiv.org",
                "http_status": 200,
                "method": "arxiv-id-match",
            }
            unverified.published_at = fixed_now - timedelta(hours=1)
            unverified.metadata["llm_screening"] = {
                "selected": True,
                "rule_version": LLM_SCREENING_RULE_VERSION,
                "prompt_version": "2026-08-30-v2",
            }

            for item in (news, paper_today, paper_old, unverified):
                database.upsert_item(item)

            output = root / "site"
            paths = build_static_site(
                load_settings(),
                output,
                database=database,
                now=fixed_now,
                site_url="https://example.github.io/daily-ai-radar/",
            )

            self.assertEqual(len(paths), 7)
            self.assertTrue(all(path.exists() for path in paths))
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn(news.title, html)
            self.assertIn(paper_today.title, html)
            self.assertIn(paper_old.title, html)
            self.assertNotIn(unverified.title, html)
            self.assertIn('data-today="true"', html)
            self.assertIn('data-today="false"', html)
            self.assertIn("社区热度", html)
            self.assertIn("讨论信号，不代表帖内事实已获官方证实", html)

            payload = json.loads(
                (output / "data" / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["counts"]["news"], 1)
            self.assertEqual(len(payload["papers_today"]), 1)
            self.assertEqual(len(payload["papers_recent"]), 2)
            self.assertEqual(payload["papers_today"][0]["title"], paper_today.title)
            self.assertEqual(payload["source_health"]["total"], 0)

            markdown = (output / "daily.md").read_text(encoding="utf-8")
            self.assertIn(paper_today.title, markdown)
            self.assertNotIn(paper_old.title, markdown)
            rss = ET.parse(output / "feed.xml")
            self.assertEqual(
                rss.findtext("./channel/link"),
                "https://example.github.io/daily-ai-radar/",
            )
            self.assertEqual(
                rss.findtext("./channel/lastBuildDate"),
                "Sat, 29 Aug 2026 01:30:00 GMT",
            )


if __name__ == "__main__":
    unittest.main()
