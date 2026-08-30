import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daily_radar.config import LLMSettings
from daily_radar.db import Database
from daily_radar.llm import (
    DeepSeekScreener,
    LLMBudgetExceeded,
    LLMResponseError,
    estimate_deepseek_cost,
)
from daily_radar.models import RadarItem


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def make_item(kind="news"):
    return RadarItem(
        kind=kind,
        title=(
            "Lab releases a new multimodal reasoning model"
            if kind == "news"
            else "DriveVLA: Vision-Language-Action for Autonomous Driving"
        ),
        summary=(
            "The model weights and benchmark results are now available."
            if kind == "news"
            else "A VLA driving policy is evaluated in closed-loop autonomous driving."
        ),
        url="https://example.com/item",
        canonical_url="https://example.com/item",
        source_id="official",
        source_name="Official Lab",
        source_tier=1,
        source_type="official" if kind == "news" else "paper-api",
        source_focus=1.0,
        published_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        metadata={"provenance": {"status": "verified-primary"}},
    )


def llm_settings(**overrides):
    values = {
        "api_key": "secret-for-test",
        "max_retries": 0,
        "system_prompt_path": ROOT / "prompts" / "system.md",
        "news_prompt_path": ROOT / "prompts" / "news_screening.md",
        "paper_prompt_path": ROOT / "prompts" / "paper_screening.md",
    }
    values.update(overrides)
    return LLMSettings(**values)


class DeepSeekScreenerTests(unittest.TestCase):
    def test_rejects_model_evidence_not_present_in_source_text(self):
        with self.assertRaises(LLMResponseError):
            DeepSeekScreener._validate_evidence(
                [make_item()],
                [{"selected": True, "evidence": ["a result never supplied"]}],
            )

    def test_uses_v4_pro_thinking_max_and_records_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            captured = {}
            decision = {
                "items": [
                    {
                        "id": "n001-000",
                        "selected": True,
                        "is_ai": True,
                        "is_concrete_release_or_result": True,
                        "importance_score": 91,
                        "confidence": 0.95,
                        "category": "model-release",
                        "summary_zh": "一个新的多模态推理模型已正式发布。",
                        "why_important": "权重与评测结果同时开放。",
                        "evidence": [
                            "model weights and benchmark results are now available"
                        ],
                        "tags": ["multimodal", "open-weights"],
                        "dimension_scores": {
                            "semantic_relevance": 98,
                            "novelty": 90,
                            "impact": 88,
                            "evidence_quality": 85,
                        },
                    }
                ]
            }
            response = {
                "id": "response-test",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(decision)},
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "prompt_cache_hit_tokens": 400,
                    "prompt_cache_miss_tokens": 600,
                    "total_tokens": 1500,
                    "completion_tokens_details": {"reasoning_tokens": 300},
                },
            }

            def opener(request, timeout):
                captured["url"] = request.full_url
                captured["timeout"] = timeout
                captured["body"] = json.loads(request.data.decode("utf-8"))
                return FakeResponse(response)

            clock = lambda: datetime(2026, 8, 29, 0, 37, tzinfo=timezone.utc)
            screener = DeepSeekScreener(
                llm_settings(), database, "Asia/Shanghai", opener=opener, clock=clock
            )
            item = make_item()
            screened = screener.screen([item], "news")

            self.assertEqual(screened, [item])
            self.assertTrue(item.metadata["llm_screening"]["selected"])
            self.assertEqual(item.metadata["llm_screening"]["reasoning_effort"], "max")
            self.assertEqual(len(item.metadata["llm_screening"]["prompt_sha256"]), 64)
            self.assertEqual(item.score, 91)
            self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
            self.assertEqual(captured["body"]["model"], "deepseek-v4-pro")
            self.assertEqual(captured["body"]["thinking"], {"type": "enabled"})
            self.assertEqual(captured["body"]["reasoning_effort"], "max")
            self.assertEqual(
                captured["body"]["response_format"], {"type": "json_object"}
            )
            self.assertNotIn("temperature", captured["body"])

            usage = database.llm_usage_summary("2026-08-29")
            self.assertEqual(usage["total_tokens"], 1500)
            self.assertEqual(usage["reasoning_tokens"], 300)
            self.assertGreater(usage["estimated_cost_usd"], 0)

    def test_paper_cannot_pass_when_application_is_not_substantive(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            decision = {
                "items": [
                    {
                        "id": "p001-000",
                        "selected": True,
                        "is_mllm_vla": True,
                        "is_autonomous_driving": True,
                        "is_substantive_application": False,
                        "importance_score": 80,
                        "confidence": 0.8,
                        "category": "other",
                        "summary_zh": "仅在背景中提到驾驶。",
                        "why_important": "不属于目标范围。",
                        "evidence": [],
                        "tags": [],
                        "dimension_scores": {
                            "mllm_vla_relevance": 80,
                            "driving_relevance": 20,
                            "method_novelty": 70,
                            "evidence_quality": 60,
                            "reproducibility": 30,
                        },
                    }
                ]
            }
            payload = {
                "id": "response-paper",
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(decision)},
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            }
            screener = DeepSeekScreener(
                llm_settings(),
                database,
                "Asia/Shanghai",
                opener=lambda request, timeout: FakeResponse(payload),
                clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
            )
            item = make_item("paper")
            screener.screen([item], "paper")
            self.assertFalse(item.metadata["llm_screening"]["selected"])

    def test_daily_token_limit_stops_before_network_call(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            database.record_llm_usage(
                {
                    "occurred_at": "2026-08-29T00:00:00+00:00",
                    "local_date": "2026-08-29",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "purpose": "existing",
                    "total_tokens": 249900,
                    "estimated_cost_usd": 0.1,
                    "status": "success",
                }
            )
            called = []
            screener = DeepSeekScreener(
                llm_settings(),
                database,
                "Asia/Shanghai",
                opener=lambda request, timeout: called.append(request),
                clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
            )
            with self.assertRaises(LLMBudgetExceeded):
                screener.screen([make_item()], "news")
            self.assertEqual(called, [])

    def test_peak_price_is_double_off_peak(self):
        off_peak = estimate_deepseek_cost(
            at=datetime(2026, 8, 29, 0, tzinfo=timezone.utc),
            cache_hit_tokens=1000,
            cache_miss_tokens=1000,
            completion_tokens=1000,
        )
        peak = estimate_deepseek_cost(
            at=datetime(2026, 8, 31, 1, tzinfo=timezone.utc),
            cache_hit_tokens=1000,
            cache_miss_tokens=1000,
            completion_tokens=1000,
        )
        self.assertAlmostEqual(peak, off_peak * 2)


if __name__ == "__main__":
    unittest.main()
