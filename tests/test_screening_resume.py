import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daily_radar.db import Database
from daily_radar.llm import DeepSeekScreener, LLMResponseError
from tests.test_llm import FakeResponse, llm_settings, make_item, paper_decision


def papers():
    result = [make_item("paper"), make_item("paper")]
    for index, item in enumerate(result):
        item.canonical_url += f"-{index}"
        item.title += f" Study {index}"
    return result


class ScreeningResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "radar.db")
        self.database.initialize()
        self.calls = []
        self.prompts = []
        self.bad_responses = 0

    def opener(self, request, timeout):
        body = json.loads(request.data)
        prompt = body["messages"][1]["content"]
        self.prompts.append(prompt)
        candidates, _ = json.JSONDecoder().raw_decode(prompt.rsplit("候选数据：", 1)[1].strip())
        self.calls.append(candidates)
        values = []
        for item in candidates:
            if item["id"].startswith("t"):
                values.append({"id": item["id"], "candidate": True, "confidence": 0.9})
            else:
                decision = paper_decision(id=item["id"])
                if item["title"].endswith("Study 1") and self.bad_responses:
                    self.bad_responses -= 1
                    decision["evidence"] = ["a paraphrase that is absent from the paper"]
                values.append(decision)
        return FakeResponse({
            "id": f"response-{len(self.calls)}", "model": "deepseek-v4-pro",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": values})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        })

    def screener(self, **overrides):
        return DeepSeekScreener(
            llm_settings(**overrides), self.database, "Asia/Shanghai", opener=self.opener,
            clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        )

    def test_bad_excerpt_retries_only_the_offending_paper(self):
        self.bad_responses = 2
        result = self.screener(max_retries=1).screen(papers(), "paper")
        self.assertEqual([len(call) for call in self.calls], [2, 1, 1])
        self.assertIn("a paraphrase that is absent from the paper", self.prompts[1])
        self.assertTrue(all(call[0]["title"].endswith("Study 1") for call in self.calls[1:]))
        self.assertTrue(all(item.metadata["llm_screening"]["selected"] for item in result))
        self.screener().screen(papers(), "paper")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.database.llm_usage_summary("2026-09-26")["total_tokens"], 600)

    def test_failed_run_resumes_triage_and_valid_final_decisions(self):
        self.bad_responses = 3
        with self.assertRaisesRegex(LLMResponseError, "unsupported_excerpts"):
            self.screener(max_retries=1).screen_papers_two_stage(papers())
        self.assertEqual([len(call) for call in self.calls], [2, 2, 1, 1])
        self.assertEqual(self.database.stats()["total"], 0)
        # A fresh process/database connection restores all validated work,
        # while the failed item itself must still get a new model decision.
        self.database = Database(self.database.path)
        resumed = self.screener().screen_papers_two_stage(papers())
        self.assertEqual([len(call) for call in self.calls], [2, 2, 1, 1, 1])
        self.assertTrue(self.calls[-1][0]["title"].endswith("Study 1"))
        self.assertEqual(len(resumed), 2)
        self.assertEqual(self.database.llm_usage_summary("2026-09-26")["total_tokens"], 1000)
        self.assertEqual(self.database.llm_usage_summary("2026-09-25")["total_tokens"], 0)

    def test_content_and_policy_changes_invalidate_checkpoints(self):
        self.screener().screen_papers_two_stage(papers())
        self.assertEqual(len(self.calls), 2)
        self.screener().screen_papers_two_stage(papers())
        self.assertEqual(len(self.calls), 2)
        changed = papers()
        changed[0].summary += " Additional experiments are reported."
        self.screener().screen_papers_two_stage(changed)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual([len(call) for call in self.calls[-2:]], [1, 1])
        self.screener(prompt_version="new-policy").screen_papers_two_stage(papers())
        self.assertEqual(len(self.calls), 6)

    def test_invalid_evidence_is_never_cached_as_a_selected_result(self):
        self.bad_responses = 2
        item = papers()[1]
        screener = self.screener(max_retries=1)
        with self.assertRaises(LLMResponseError):
            screener.screen([item], "paper")
        self.assertIsNone(self.database.screening_checkpoint(screener._checkpoint_key(item, "paper")))
        self.assertEqual(self.database.list_items(eligible_only=True), [])


if __name__ == "__main__":
    unittest.main()
