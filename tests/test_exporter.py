import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_radar.db import Database
from daily_radar.exporter import export_all
from daily_radar.sample import build_demo_items, seed_demo


class ExporterTests(unittest.TestCase):
    def test_exports_three_formats_and_excludes_unverified_demo_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "radar.db")
            seed_demo(database)
            paths = export_all(database, root / "out")
            self.assertEqual({path.name for path in paths}, {"latest.json", "daily.md", "feed.xml"})
            payload = json.loads((root / "out" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 0)

    def test_daily_export_does_not_backfill_old_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "radar.db")
            database.initialize()
            current = build_demo_items()[4]
            older = deepcopy(current)
            now = datetime.now(timezone.utc)
            current.published_at = now
            current.metadata["demo"] = False
            current.metadata["provenance"] = {"status": "verified-primary"}
            current.url += "-today"
            current.canonical_url += "-today"
            older.published_at = now - timedelta(days=3)
            older.metadata["demo"] = False
            older.metadata["provenance"] = {"status": "verified-primary"}
            older.url += "-old"
            older.canonical_url += "-old"
            database.upsert_item(current)
            database.upsert_item(older)

            cutoff = now - timedelta(days=1)
            export_all(
                database,
                root / "out",
                {"news": cutoff, "paper": cutoff},
            )
            payload = json.loads(
                (root / "out" / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["url"], current.url)
            self.assertEqual(payload["published_since"]["paper"], cutoff.isoformat())


if __name__ == "__main__":
    unittest.main()
