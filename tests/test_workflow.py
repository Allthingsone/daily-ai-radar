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

    def test_extra_budget_is_opt_in_and_scoped_to_one_forced_manual_run(self):
        path = ROOT / ".github" / "workflows" / "pages.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        budget_input = workflow["on"]["workflow_dispatch"]["inputs"]["rerun_budget"]
        self.assertEqual(budget_input["type"], "boolean")
        self.assertEqual(budget_input["default"], "false")
        steps = workflow["jobs"]["build"]["steps"]
        budget_step = next(
            step for step in steps
            if step.get("name") == "Apply approved one-run budget override"
        )
        for guard in (
            "steps.daily-guard.outputs.should_run == 'true'",
            "github.event_name == 'workflow_dispatch'",
            "inputs.force && inputs.rerun_budget",
        ):
            self.assertIn(guard, budget_step["if"])
        self.assertIn("DAILY_RADAR_LLM_DAILY_TOKEN_LIMIT=700000", budget_step["run"])
        self.assertIn("DAILY_RADAR_LLM_DAILY_COST_LIMIT_USD=1.50", budget_step["run"])
        self.assertIn('>> "$GITHUB_ENV"', budget_step["run"])
        names = [step["name"] for step in steps]
        self.assertLess(names.index("Run offline tests"), names.index(budget_step["name"]))
        self.assertLess(
            names.index("Restore today's published DeepSeek usage"),
            names.index(budget_step["name"]),
        )
        self.assertLess(
            names.index(budget_step["name"]), names.index("Collect papers and finalize digest")
        )
        defaults = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        self.assertEqual(defaults["llm"]["daily_token_limit"], 500000)
        self.assertEqual(defaults["llm"]["daily_cost_limit_usd"], 1.0)

    def test_dated_replay_keeps_billing_cache_and_delivery_dates_separate(self):
        path = ROOT / ".github" / "workflows" / "pages.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertIn("inputs.target_date", workflow["run-name"])
        self.assertIn(" · replay {0}", workflow["run-name"])
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["target_date"]["default"], "")
        build = workflow["jobs"]["build"]
        steps = {step["name"]: step for step in build["steps"]}
        self.assertIn("outputs.digest_date", build["outputs"]["local_date"])
        self.assertIn("outputs.digest_date", steps["Restore today's successful-delivery marker"]["with"]["key"])
        cache = steps["Restore same-day radar state"]["with"]
        self.assertIn("outputs.value", cache["key"])
        self.assertLess(cache["restore-keys"].index("outputs.value"), cache["restore-keys"].index("outputs.digest_date"))
        self.assertIn("outputs.value", steps["Save same-day radar state, including failed-call usage"]["with"]["key"])
        self.assertIn('collect --kind publish --date "$TARGET_DATE"', steps["Collect papers and finalize digest"]["run"])
        self.assertIn('--date "$TARGET_DATE"', steps["Build static site"]["run"])
        self.assertIn('--date "$TARGET_DATE"', steps["Send daily email"]["run"])
        self.assertIn("--include-root", steps["Preserve published historical archives"]["run"])
        date_script = steps["Compute Shanghai usage date"]["run"].split("\n", 1)[1].rsplit("\nPY", 1)[0]
        compile(date_script, "workflow date validation", "exec")
        self.assertIn("parsed >= today", date_script)
        self.assertIn('os.environ["FORCE_RUN"] != "true"', date_script)

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
