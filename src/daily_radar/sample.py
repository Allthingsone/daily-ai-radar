from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from .config import LLMSettings
from .db import Database
from .eligibility import LLM_SCREENING_RULE_VERSION
from .models import RadarItem
from .paper_policy import PAPER_CATEGORY_LABELS, PAPER_DIMENSION_LABELS, PAPER_FLAGS
from .processing.normalize import canonicalize_url, fingerprint_title
from .processing.scoring import score_news


def _base_item(
    kind: str,
    slug: str,
    title: str,
    summary: str,
    hours_ago: int,
    source_name: str,
    source_type: str,
) -> RadarItem:
    url = f"https://example.com/daily-ai-radar-demo/{slug}"
    return RadarItem(
        kind=kind,
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalize_url(url),
        source_id=f"demo-{source_name.lower().replace(' ', '-')}",
        source_name=source_name,
        source_tier=1 if source_type == "official" else 2,
        source_type=source_type,
        source_focus=1.0,
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        fingerprint=fingerprint_title(title),
        cluster_key=fingerprint_title(title),
        metadata={"demo": True, "source_count": 1},
        tags=["DEMO"],
    )


def build_demo_items() -> List[RadarItem]:
    news = [
        _base_item(
            "news",
            "open-multimodal-model",
            "[演示] 新一代开源多模态推理模型发布",
            "A research lab announces an open-source multimodal large language model "
            "with vision-language reasoning and a new evaluation benchmark.",
            2,
            "演示官方源",
            "official",
        ),
        _base_item(
            "news",
            "agent-sdk-release",
            "[演示] AI Agent SDK 增加可观测性与安全沙箱",
            "The release adds tracing, tool permissions and security controls for AI agent applications.",
            7,
            "演示 GitHub Release",
            "github-release",
        ),
        _base_item(
            "news",
            "ai-safety-policy",
            "[演示] 新的生成式 AI 安全评测与治理框架公布",
            "The policy framework introduces model safety benchmarks, incident reporting and governance guidance.",
            14,
            "演示研究机构",
            "official",
        ),
        _base_item(
            "news",
            "autonomous-driving-vla",
            "[演示] 自动驾驶 Vision-Language-Action 模型开放技术预览",
            "A new VLA foundation model for autonomous driving combines multimodal reasoning and trajectory planning.",
            19,
            "演示自动驾驶实验室",
            "official",
        ),
    ]
    for item in news:
        score_news(item, ["multimodal", "agent", "autonomous driving"])
        item.is_important = item.score >= 45
        item.metadata["llm_screening"] = {
            "selected": True,
            "rule_version": LLM_SCREENING_RULE_VERSION,
            "provider": "demo",
            "prompt_version": LLMSettings().prompt_version,
        }
    news[0].metadata["community_signals"] = [
        {
            "platform": "Hacker News",
            "signal_type": "points-comments-rank",
            "rank": 4,
            "points": 156,
            "comments": 48,
            "qualified": True,
            "discussion_url": "https://example.com/daily-ai-radar-demo/discussion",
        }
    ]

    paper_specs = [
        (
            "vla-action-tokens",
            "[演示论文] Shared Action Representations for Vision-Language-Action Policies",
            "We learn shared action tokens for vision-language-action policies. "
            "Controlled experiments study cross-embodiment transfer for robot arm manipulation.",
            4,
            "vla-representation", ("is_vla", "is_representation_research"),
        ),
        (
            "wam-world-state",
            "[演示论文] Spatial State Representations for World Action Models",
            "A world action model jointly predicts robot actions and future observations. "
            "We compare spatial and visual latent states for policy learning and dynamics prediction.",
            11,
            "wam-representation", ("is_world_action_model", "is_representation_research"),
        ),
        (
            "multimodal-embedding",
            "[演示论文] Universal Multimodal Embedding Model Technical Report",
            "We release a reusable text, image and video embedding model with public weights. "
            "Systematic multilingual retrieval evaluations show substantial gains across tasks.",
            27,
            "embedding-model", ("is_embedding_model", "is_model_release", "is_significant_contribution"),
        ),
        (
            "manipulation-benchmark",
            "[演示论文] A Reproducible Benchmark for Bimanual Robot Manipulation",
            "We introduce a robot arm manipulation benchmark spanning diverse long-horizon tasks. "
            "Reusable protocols and systematic baseline comparisons evaluate robustness and generalization.",
            31,
            "manipulation-benchmark", ("is_robot_manipulation", "is_benchmark", "is_significant_contribution"),
        ),
    ]
    papers: List[RadarItem] = []
    for index, (slug, title, abstract, hours_ago, category, active_flags) in enumerate(paper_specs):
        item = _base_item(
            "paper", slug, title, abstract, hours_ago, "arXiv 演示数据", "paper-api"
        )
        item.external_id = f"demo.0000{index + 1}"
        item.authors = ["Demo Author", "Radar Preview Team"]
        item.categories = ["cs.CV", "cs.RO"]
        item.metadata.update(
            {
                "code_url": "https://github.com/example/demo-only",
                "pdf_url": "",
            }
        )
        # Explicitly fictional display fixtures, never a production fallback.
        item.category = category
        item.score = 88 - index * 3
        item.is_important = True
        item.tags = ["DEMO", category]
        item.component_scores = dict.fromkeys(PAPER_DIMENSION_LABELS, 0)
        relevance = (
            ("vla_wam_relevance", "representation_relevance") if index < 2
            else ("embedding_relevance",) if index == 2
            else ("manipulation_benchmark_relevance",)
        )
        item.component_scores.update(dict.fromkeys(relevance, 90))
        item.component_scores.update(method_novelty=80, evidence_quality=80, reproducibility=60)
        flags = dict.fromkeys(PAPER_FLAGS, False)
        flags.update(dict.fromkeys(active_flags, True))
        flags["is_substantive_contribution"] = True
        item.metadata["summary_zh"] = "离线演示：" + PAPER_CATEGORY_LABELS[category] + "，非真实论文。"
        item.reasons = ["离线演示数据，不代表真实模型筛选结果"]
        item.metadata["llm_screening"] = {
            "selected": True, "rule_version": LLM_SCREENING_RULE_VERSION,
            "provider": "demo", "prompt_version": LLMSettings().prompt_version,
            "flags": flags,
        }
        papers.append(item)
    return news + papers


def seed_demo(database: Database) -> int:
    database.initialize()
    items = build_demo_items()
    for item in items:
        database.upsert_item(item)
    return len(items)
