import unittest
from datetime import datetime, timezone

from daily_radar.time_windows import build_period_window, paper_reference


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

    def test_previous_announcement_day_has_both_bounds_and_does_not_shift_news(self):
        for now, expected in ((datetime(2026, 11, 17, 23, tzinfo=timezone.utc), "2026-11-17"),
                              (datetime(2027, 1, 1, 0, tzinfo=timezone.utc), "2026-12-31"),
                              (datetime(2026, 3, 9, 0, tzinfo=timezone.utc), "2026-03-08")):
            with self.subTest(now=now):
                window = build_period_window("paper", "today", "Asia/Shanghai", 96, now, paper_lag_days=1)
                self.assertEqual(window.local_date, expected)
                self.assertEqual((window.published_before - window.published_since).total_seconds(), 86400)
                self.assertLess(paper_reference(now, "Asia/Shanghai"), now)
                news = build_period_window("news", "recent", "Asia/Shanghai", 48, now, paper_lag_days=1)
                original = build_period_window("news", "recent", "Asia/Shanghai", 48, now)
                self.assertEqual(news, original)


if __name__ == "__main__":
    unittest.main()
