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


def news_decision(category, **overrides):
    value = {
        "selected": True,
        "is_ai": True,
        "is_major_foundation_model": False,
        "is_significant_product_tool_or_hardware": False,
        "is_autonomous_driving_dataset_or_benchmark": False,
        "is_important_research_result": False,
        "is_community_trending": False,
        "has_verifiable_heat_signal": False,
        "importance_score": 80,
        "confidence": 0.9,
        "category": category,
        "summary_zh": "社区正在讨论一项具有技术价值的 AI 议题。",
        "why_important": "互动指标达到配置门槛。",
        "evidence": ["multimodal reasoning model"],
        "tags": [],
        "dimension_scores": {
            "semantic_relevance": 90,
            "novelty": 70,
            "impact": 80,
            "community_heat": 85,
            "evidence_quality": 75,
        },
    }
    value.update(overrides)
    return value


def paper_decision(**overrides):
    value = {
        "selected": True,
        "is_mllm_vla": True,
        "is_autonomous_driving": True,
        "is_vln": False,
        "is_indoor_navigation": False,
        "is_substantive_application": True,
        "importance_score": 85,
        "confidence": 0.9,
        "category": "vla-policy",
        "summary_zh": "提出并评测面向目标应用的导航方法。",
        "why_important": "方法与实验直接服务目标任务。",
        "evidence": ["closed-loop autonomous driving"],
        "tags": [],
        "dimension_scores": {
            "mllm_vla_relevance": 90,
            "driving_relevance": 90,
            "vln_relevance": 0,
            "indoor_navigation_relevance": 0,
            "method_novelty": 80,
            "evidence_quality": 80,
            "reproducibility": 60,
        },
    }
    value.update(overrides)
    return value


class DeepSeekScreenerTests(unittest.TestCase):
    def test_paper_scope_preserves_driving_and_adds_both_vln_scenes(self):
        cases = [
            ("existing driving VLA", {}, True),
            ("classic indoor VLN", {
                "is_mllm_vla": False, "is_autonomous_driving": False,
                "is_vln": True, "is_indoor_navigation": True,
                "category": "vision-language-navigation",
            }, True),
            ("driving VLN without large models", {
                "is_mllm_vla": False, "is_vln": True,
                "category": "vision-language-navigation",
            }, True),
            ("indoor VLN benchmark", {
                "is_mllm_vla": False, "is_autonomous_driving": False,
                "is_vln": True, "is_indoor_navigation": True,
                "category": "benchmark-dataset",
            }, True),
            ("tabletop VLA", {"is_autonomous_driving": False}, False),
            ("driving without multimodal models or VLN", {"is_mllm_vla": False}, False),
            ("indoor navigation without VLN", {
                "is_mllm_vla": False, "is_autonomous_driving": False,
                "is_indoor_navigation": True,
            }, False),
            ("VLN without a supported scene", {
                "is_mllm_vla": False, "is_autonomous_driving": False,
                "is_vln": True,
            }, False),
            ("incidental indoor VLN mention", {
                "is_mllm_vla": False, "is_autonomous_driving": False,
                "is_vln": True, "is_indoor_navigation": True,
                "is_substantive_application": False,
            }, False),
            ("model rejection remains rejected", {"selected": False}, False),
        ]
        for label, flags, expected in cases:
            with self.subTest(label=label):
                decision = DeepSeekScreener._normalize_decision(
                    paper_decision(**flags), "paper"
                )
                self.assertEqual(decision["selected"], expected)

    def test_vln_fields_require_json_booleans(self):
        for field in ("is_vln", "is_indoor_navigation"):
            for invalid in (None, "true", 1):
                with self.subTest(field=field, invalid=invalid):
                    raw = paper_decision()
                    if invalid is None:
                        raw.pop(field)
                    else:
                        raw[field] = invalid
                    with self.assertRaisesRegex(LLMResponseError, field):
                        DeepSeekScreener._normalize_decision(raw, "paper")

    def test_vln_scores_are_required_and_bounded(self):
        for field in ("vln_relevance", "indoor_navigation_relevance"):
            for invalid in (None, -1, 101, True):
                with self.subTest(field=field, invalid=invalid):
                    raw = paper_decision()
                    if invalid is None:
                        raw["dimension_scores"].pop(field)
                    else:
                        raw["dimension_scores"][field] = invalid
                    with self.assertRaises(LLMResponseError):
                        DeepSeekScreener._normalize_decision(raw, "paper")

    def test_news_routes_enforce_the_user_specific_hard_gates(self):
        routine_model = DeepSeekScreener._normalize_decision(
            news_decision("model-release"), "news"
        )
        unrelated_dataset = DeepSeekScreener._normalize_decision(
            news_decision("dataset-benchmark"), "news"
        )
        unheated_research = DeepSeekScreener._normalize_decision(
            news_decision(
                "research-result",
                is_important_research_result=True,
                has_verifiable_heat_signal=True,
            ),
            "news",
            verified_heat_signal=False,
        )

        self.assertFalse(routine_model["selected"])
        self.assertFalse(unrelated_dataset["selected"])
        self.assertFalse(unheated_research["selected"])
        self.assertFalse(
            unheated_research["flags"]["has_verifiable_heat_signal"]
        )

    def test_community_route_requires_collector_verified_heat(self):
        accepted = DeepSeekScreener._normalize_decision(
            news_decision(
                "community-trending",
                is_community_trending=True,
                has_verifiable_heat_signal=True,
            ),
            "news",
            verified_heat_signal=True,
        )
        self.assertTrue(accepted["selected"])

    def test_community_source_cannot_become_release_proof(self):
        rejected = DeepSeekScreener._normalize_decision(
            news_decision(
                "model-release",
                is_major_foundation_model=True,
                has_verifiable_heat_signal=True,
            ),
            "news",
            verified_heat_signal=True,
            community_source=True,
        )
        self.assertFalse(rejected["selected"])

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
                        "is_major_foundation_model": True,
                        "is_significant_product_tool_or_hardware": False,
                        "is_autonomous_driving_dataset_or_benchmark": False,
                        "is_important_research_result": False,
                        "is_community_trending": False,
                        "has_verifiable_heat_signal": False,
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
                            "community_heat": 0,
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
            self.assertEqual(captured["body"]["max_tokens"], 32768)
            self.assertEqual(
                captured["body"]["response_format"], {"type": "json_object"}
            )
            self.assertNotIn("temperature", captured["body"])

            usage = database.llm_usage_summary("2026-08-29")
            self.assertEqual(usage["total_tokens"], 1500)
            self.assertEqual(usage["reasoning_tokens"], 300)
            self.assertGreater(usage["estimated_cost_usd"], 0)

    def test_length_truncation_splits_batch_without_identical_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            calls = []

            def payload(finish_reason, identifier=""):
                content = ""
                if finish_reason == "stop":
                    content = json.dumps(
                        {
                            "items": [
                                {
                                    "id": identifier,
                                    "selected": False,
                                    "is_ai": True,
                                    "is_major_foundation_model": False,
                                    "is_significant_product_tool_or_hardware": False,
                                    "is_autonomous_driving_dataset_or_benchmark": False,
                                    "is_important_research_result": False,
                                    "is_community_trending": False,
                                    "has_verifiable_heat_signal": False,
                                    "importance_score": 20,
                                    "confidence": 0.9,
                                    "category": "not-relevant",
                                    "summary_zh": "",
                                    "why_important": "",
                                    "evidence": [],
                                    "tags": [],
                                    "dimension_scores": {
                                        "semantic_relevance": 40,
                                        "novelty": 10,
                                        "impact": 10,
                                        "community_heat": 0,
                                        "evidence_quality": 50,
                                    },
                                }
                            ]
                        }
                    )
                return {
                    "id": f"response-{len(calls)}",
                    "model": "deepseek-v4-pro",
                    "choices": [
                        {
                            "finish_reason": finish_reason,
                            "message": {"content": content},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 100,
                        "total_tokens": 200,
                    },
                }

            responses = [
                payload("length"),
                payload("stop", "n001a-000"),
                payload("stop", "n001b-000"),
            ]

            def opener(request, timeout):
                calls.append(json.loads(request.data.decode("utf-8")))
                return FakeResponse(responses[len(calls) - 1])

            screener = DeepSeekScreener(
                llm_settings(max_retries=1),
                database,
                "Asia/Shanghai",
                opener=opener,
                clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
            )
            first, second = make_item(), make_item()
            second.canonical_url += "-second"
            screened = screener.screen([first, second], "news")

            self.assertEqual(len(screened), 2)
            self.assertEqual(len(calls), 3)
            self.assertIn("n001a-000", calls[1]["messages"][1]["content"])
            self.assertIn("n001b-000", calls[2]["messages"][1]["content"])
            usage = database.llm_usage_summary("2026-08-29")
            self.assertEqual(usage["total_tokens"], 600)
            self.assertEqual(usage["failed_calls"], 1)
            self.assertEqual(usage["successful_calls"], 2)

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
                        "is_vln": False,
                        "is_indoor_navigation": False,
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
                            "vln_relevance": 0,
                            "indoor_navigation_relevance": 0,
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

    def test_all_daily_papers_get_compact_triage_before_strict_screening(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            calls = []
            triage = {
                "items": [
                    {"id": "t001-000", "candidate": True, "confidence": 0.72},
                    {"id": "t001-001", "candidate": False, "confidence": 0.98},
                ]
            }
            final = {
                "items": [
                    {
                        "id": "p001-000",
                        "selected": True,
                        "is_mllm_vla": True,
                        "is_autonomous_driving": True,
                        "is_vln": False,
                        "is_indoor_navigation": False,
                        "is_substantive_application": True,
                        "importance_score": 88,
                        "confidence": 0.91,
                        "category": "vla-policy",
                        "summary_zh": "论文提出并评测了自动驾驶 VLA 策略。",
                        "why_important": "两个目标方向均为方法和实验核心。",
                        "evidence": [
                            "VLA driving policy is evaluated in closed-loop autonomous driving"
                        ],
                        "tags": ["VLA", "autonomous-driving"],
                        "dimension_scores": {
                            "mllm_vla_relevance": 95,
                            "driving_relevance": 98,
                            "vln_relevance": 0,
                            "indoor_navigation_relevance": 0,
                            "method_novelty": 82,
                            "evidence_quality": 88,
                            "reproducibility": 65,
                        },
                    }
                ]
            }

            def response(content, index):
                return FakeResponse(
                    {
                        "id": f"response-{index}",
                        "model": "deepseek-v4-pro",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(content)},
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                            "total_tokens": 150,
                        },
                    }
                )

            responses = [response(triage, 1), response(final, 2)]

            def opener(request, timeout):
                calls.append(json.loads(request.data.decode("utf-8")))
                return responses[len(calls) - 1]

            screener = DeepSeekScreener(
                llm_settings(
                    paper_triage_batch_size=10,
                    paper_triage_abstract_chars=80,
                ),
                database,
                "Asia/Shanghai",
                opener=opener,
                clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc),
            )
            relevant = make_item("paper")
            unrelated = make_item("paper")
            unrelated.title = "A theorem about prime numbers"
            unrelated.summary = "We prove a result in analytic number theory."

            screened = screener.screen_papers_two_stage([relevant, unrelated])

            self.assertEqual(screened, [relevant, unrelated])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoning_effort", calls[0])
            self.assertEqual(calls[0]["max_tokens"], 8192)
            self.assertEqual(calls[1]["thinking"], {"type": "enabled"})
            self.assertEqual(calls[1]["reasoning_effort"], "max")
            self.assertTrue(relevant.metadata["paper_triage"]["candidate"])
            self.assertTrue(relevant.metadata["llm_screening"]["selected"])
            self.assertFalse(unrelated.metadata["paper_triage"]["candidate"])
            self.assertEqual(
                unrelated.metadata["llm_screening"]["stage"], "paper-triage"
            )
            usage = database.llm_usage_summary("2026-08-29")
            self.assertEqual(usage["calls"], 2)
            self.assertEqual(usage["request_items"], 3)
            stages = {
                item["stage"]: item
                for item in database.llm_usage_breakdown("2026-08-29")
            }
            self.assertEqual(stages["paper_triage"]["request_items"], 2)
            self.assertEqual(stages["paper_final"]["request_items"], 1)

    def test_indoor_and_driving_vln_survive_two_stages_and_feed_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            settings = llm_settings()
            calls = []
            papers = [make_item("paper"), make_item("paper")]
            papers[0].title = "Visual Instruction Following for Indoor Navigation"
            papers[0].summary = (
                "An agent follows natural-language instructions using camera observations "
                "to navigate indoor rooms. We evaluate a recurrent navigation policy."
            )
            papers[1].title = "Vision-Language Navigation for Autonomous Vehicles"
            papers[1].summary = (
                "Autonomous vehicles follow language route instructions using camera "
                "observations. We evaluate vision-language navigation in driving scenes."
            )
            final_decisions = []
            for index, item in enumerate(papers):
                item.url = item.canonical_url = f"https://arxiv.org/abs/2609.0000{index}"
                final_decisions.append(paper_decision(
                    id=f"p001-{index:03d}",
                    is_mllm_vla=False,
                    is_autonomous_driving=index == 1,
                    is_vln=True,
                    is_indoor_navigation=index == 0,
                    category="vision-language-navigation",
                    evidence=["navigate indoor rooms" if index == 0 else "language route instructions"],
                    dimension_scores={
                        "mllm_vla_relevance": 0,
                        "driving_relevance": 100 if index == 1 else 0,
                        "vln_relevance": 100,
                        "indoor_navigation_relevance": 100 if index == 0 else 0,
                        "method_novelty": 80,
                        "evidence_quality": 80,
                        "reproducibility": 60,
                    },
                ))
            contents = [
                {"items": [
                    {"id": f"t001-{index:03d}", "candidate": True, "confidence": 0.9}
                    for index in range(2)
                ]},
                {"items": final_decisions},
            ]

            def opener(request, timeout):
                calls.append(json.loads(request.data.decode("utf-8")))
                return FakeResponse({
                    "id": f"vln-response-{len(calls)}",
                    "model": "deepseek-v4-pro",
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(contents[len(calls) - 1])},
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                })

            screener = DeepSeekScreener(
                settings, database, "Asia/Shanghai", opener=opener,
                clock=lambda: datetime(2026, 9, 17, tzinfo=timezone.utc),
            )
            screened = screener.screen_papers_two_stage(papers)
            self.assertEqual(len(calls), 2)
            for call in calls:
                prompt = call["messages"][1]["content"]
                self.assertIn("VLN", prompt)
                self.assertIn("室内", prompt)
                for item in papers:
                    self.assertIn(item.title, prompt)
            for item in screened:
                self.assertTrue(item.metadata["llm_screening"]["selected"])
                self.assertFalse(item.metadata["llm_screening"]["flags"]["is_mllm_vla"])
                self.assertEqual(item.category, "vision-language-navigation")
                database.upsert_item(item)
            stored = database.list_items(
                kind="paper", eligible_only=True, verified_only=True,
                prompt_version=settings.prompt_version,
            )
            self.assertEqual({item["title"] for item in stored}, {item.title for item in papers})
            self.assertEqual(papers[0].component_scores["driving_relevance"], 0)
            self.assertEqual(papers[0].score, 85)

    def test_daily_token_limit_stops_before_network_call(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            settings = llm_settings()
            database.record_llm_usage(
                {
                    "occurred_at": "2026-08-29T00:00:00+00:00",
                    "local_date": "2026-08-29",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "purpose": "existing",
                    "total_tokens": settings.daily_token_limit - 100,
                    "estimated_cost_usd": 0.1,
                    "status": "success",
                }
            )
            called = []
            screener = DeepSeekScreener(
                settings,
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
