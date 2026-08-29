import unittest
from datetime import datetime, timezone

from daily_radar.time_windows import build_period_window


class PeriodWindowTests(unittest.TestCase):
    def test_paper_auto_means_local_calendar_day(self):
        now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        window = build_period_window("paper", "auto", "Asia/Shanghai", 96, now)
        self.assertEqual(window.period, "today")
        self.assertEqual(window.local_date, "2026-08-03")
        self.assertEqual(
            window.published_since,
            datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc),
        )
        july_paper = datetime(2026, 7, 31, 6, 10, tzinfo=timezone.utc)
        self.assertLess(july_paper, window.published_since)

    def test_recent_window_remains_available_for_backfill(self):
        now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        window = build_period_window("paper", "recent", "Asia/Shanghai", 96, now)
        self.assertEqual(window.period, "recent")
        self.assertEqual(window.label, "近 4 日")
        self.assertEqual(
            window.published_since,
            datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        )

    def test_news_auto_keeps_rolling_window(self):
        now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        window = build_period_window("news", "auto", "Asia/Shanghai", 48, now)
        self.assertEqual(window.period, "recent")
        self.assertEqual(window.label, "近 2 日")


if __name__ == "__main__":
    unittest.main()
