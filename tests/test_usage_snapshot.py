import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.usage_snapshot import restore_usage_snapshot


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class UsageSnapshotTests(unittest.TestCase):
    def test_restores_same_day_usage_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            payload = {
                "llm_usage": {
                    "local_date": "2026-08-29",
                    "model": "deepseek-v4-pro",
                    "calls": 3,
                    "request_items": 30,
                    "prompt_tokens": 10000,
                    "completion_tokens": 5000,
                    "reasoning_tokens": 3000,
                    "cache_hit_tokens": 2000,
                    "cache_miss_tokens": 8000,
                    "total_tokens": 15000,
                    "estimated_cost_usd": 0.02,
                    "prompt_version": "2026-08-29-v1",
                }
            }
            opener = lambda request, timeout: FakeResponse(payload)
            now = datetime(2026, 8, 29, 0, tzinfo=timezone.utc)
            first = restore_usage_snapshot(
                database,
                load_settings(),
                "https://example.github.io/data/latest.json",
                now=now,
                opener=opener,
            )
            second = restore_usage_snapshot(
                database,
                load_settings(),
                "https://example.github.io/data/latest.json",
                now=now,
                opener=opener,
            )
            summary = database.llm_usage_summary("2026-08-29")

            self.assertTrue(first["restored"])
            self.assertFalse(second["restored"])
            self.assertEqual(summary["calls"], 3)
            self.assertEqual(summary["total_tokens"], 15000)
            self.assertAlmostEqual(summary["estimated_cost_usd"], 0.02)


if __name__ == "__main__":
    unittest.main()
