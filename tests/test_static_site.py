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
    def test_indoor_vln_is_visible_in_pages_and_daily_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "radar.db")
            database.initialize()
            settings = load_settings()
            now = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)
            paper = build_demo_items()[4]
            paper.title = "Indoor Vision-Language Navigation with Recurrent Policies"
            paper.summary = "An agent follows language instructions using visual observations in indoor rooms."
            paper.category = "vision-language-navigation"
            paper.published_at = now
            paper.component_scores = {
                "mllm_vla_relevance": 0, "driving_relevance": 0,
                "vln_relevance": 100, "indoor_navigation_relevance": 100,
            }
            paper.metadata.update({
                "demo": False,
                "summary_zh": "基于视觉观测与语言指令进行室内导航。",
                "provenance": {"status": "verified-arxiv-api"},
                "llm_screening": {
                    "selected": True, "rule_version": LLM_SCREENING_RULE_VERSION,
                    "prompt_version": settings.llm.prompt_version,
                    "flags": {
                        "is_mllm_vla": False, "is_autonomous_driving": False,
                        "is_vln": True, "is_indoor_navigation": True,
                        "is_substantive_application": True,
                    },
                },
            })
            database.upsert_item(paper)
            output = root / "site"
            build_static_site(settings, output, database=database, now=now)
            html = (output / "index.html").read_text(encoding="utf-8")
            for label in (paper.title, "VLN 视觉语言导航", "VLN 相关性", "室内导航相关性"):
                self.assertIn(label, html)
            self.assertNotIn("论文必须同时属于 MLLM/VLA 与自动驾驶", html)
            payload = json.loads((output / "data" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(payload["papers_today"]), 1)
            self.assertEqual(payload["papers_today"][0]["category"], "vision-language-navigation")
            markdown = (output / "daily.md").read_text(encoding="utf-8")
            self.assertIn(paper.title, markdown)
            self.assertIn("VLN（自动驾驶/室内导航）", markdown)
            rss = ET.parse(output / "feed.xml")
            self.assertEqual(rss.findtext("./channel/item/title"), paper.title)

    def test_builds_pages_snapshot_with_strict_today_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "radar.db")
            database.initialize()
            fixed_now = datetime(2026, 8, 29, 1, 30, tzinfo=timezone.utc)
            settings = load_settings()

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
                "prompt_version": settings.llm.prompt_version,
            }
            paper_today.published_at = fixed_now - timedelta(hours=1)
            paper_today.metadata["provenance"] = {
                "status": "verified-primary",
                "domain": "arxiv.org",
                "http_status": 200,
                "method": "arxiv-id-match",
            }
            paper_today.metadata["arxiv_first_submitted_at"] = (
                fixed_now - timedelta(days=2)
            ).isoformat()
            paper_today.metadata["arxiv_version_number"] = 1
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
                "prompt_version": settings.llm.prompt_version,
            }

            for item in (news, paper_today, paper_old, unverified):
                database.upsert_item(item)

            output = root / "site"
            paths = build_static_site(
                settings,
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
            self.assertIn("首次提交", html)
            self.assertIn("当前版本</b> v1", html)
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
