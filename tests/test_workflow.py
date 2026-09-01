from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowScheduleTests(unittest.TestCase):
    def test_daily_schedule_has_early_and_official_shanghai_attempts(self):
        path = ROOT / ".github" / "workflows" / "pages.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

        triggers = workflow["on"]
        schedules = triggers["schedule"]
        self.assertEqual(
            [entry["cron"] for entry in schedules],
            [
                "10 3 * * *",
                "30 3 * * *",
                "50 3 * * *",
                "10 4 * * *",
                "30 4 * * *",
                "10 8 * * *",
                "30 8 * * *",
                "50 8 * * *",
                "10 9 * * *",
                "30 9 * * *",
            ],
        )
        self.assertTrue(
            all(entry["timezone"] == "Asia/Shanghai" for entry in schedules)
        )
        self.assertIn("force", triggers["workflow_dispatch"]["inputs"])
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")

        jobs = workflow["jobs"]
        self.assertIn("mark-success", jobs)
        self.assertIn("daily-radar-success-", path.read_text(encoding="utf-8"))
        email_step = next(
            step
            for step in jobs["build"]["steps"]
            if step.get("name") == "Send daily email"
        )
        self.assertNotIn("continue-on-error", email_step)
        self.assertIn("exit 1", email_step["run"])


if __name__ == "__main__":
    unittest.main()
