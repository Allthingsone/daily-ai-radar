import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowScheduleTests(unittest.TestCase):
    def test_daily_schedule_keeps_five_native_shanghai_fallbacks(self):
        path = ROOT / ".github" / "workflows" / "pages.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

        triggers = workflow["on"]
        schedules = triggers["schedule"]
        self.assertEqual(
            [entry["cron"] for entry in schedules],
            [
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
        self.assertIn("phase", triggers["workflow_dispatch"]["inputs"])
        self.assertEqual(
            triggers["workflow_dispatch"]["inputs"]["phase"]["options"],
            ["publish", "news"],
        )
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")

        jobs = workflow["jobs"]
        self.assertIn("mark-success", jobs)
        self.assertIn("daily-radar-success-", path.read_text(encoding="utf-8"))
        self.assertIn("aliyun-fc/test/*.test.js", path.read_text(encoding="utf-8"))
        self.assertNotIn("cloudflare-worker", path.read_text(encoding="utf-8"))
        email_step = next(
            step
            for step in jobs["build"]["steps"]
            if step.get("name") == "Send daily email"
        )
        self.assertNotIn("continue-on-error", email_step)
        self.assertIn("exit 1", email_step["run"])

        news_step = next(
            step
            for step in jobs["build"]["steps"]
            if step.get("name") == "Pre-screen verified news"
        )
        publish_step = next(
            step
            for step in jobs["build"]["steps"]
            if step.get("name") == "Collect papers and finalize digest"
        )
        self.assertEqual(news_step["run"], "daily-radar collect --kind news")
        self.assertIn("daily-radar collect --kind publish", publish_step["run"])
        self.assertIn("daily-radar collect --kind all", publish_step["run"])
        self.assertIn("phase == 'publish'", jobs["deploy"]["if"])

    def test_aliyun_watchdog_uses_a_simple_interval_and_auto_phase(self):
        config_path = ROOT / "aliyun-fc" / "deployment-config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [trigger["cron"] for trigger in config["triggers"]],
            ["@every 10m"],
        )
        self.assertEqual(
            [trigger["payload"] for trigger in config["triggers"]],
            ['{"phase":"auto"}'],
        )
        self.assertEqual(config["function"]["runtime"], "nodejs20")
        self.assertEqual(config["function"]["handler"], "index.handler")
        self.assertEqual(config["function"]["timeout_seconds"], 60)
        self.assertTrue(config["function"]["internet_access"])
        self.assertEqual(
            config["environment_values_except_token"]["GITHUB_REPO"],
            "daily-ai-radar",
        )
        self.assertIn(
            "GITHUB_ACTIONS_TOKEN", config["required_environment_variables"]
        )
        self.assertNotIn(
            "GITHUB_ACTIONS_TOKEN", config["environment_values_except_token"]
        )


if __name__ == "__main__":
    unittest.main()
