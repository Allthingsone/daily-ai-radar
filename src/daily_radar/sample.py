from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from .db import Database
from .models import RadarItem
from .processing.normalize import canonicalize_url, fingerprint_title
from .processing.scoring import score_news, score_paper


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

    paper_specs = [
        (
            "drive-vla",
            "[演示论文] DriveVLA: Vision-Language-Action Models for End-to-End Autonomous Driving",
            "We propose a vision-language-action framework for end-to-end autonomous driving. "
            "The driving agent predicts trajectories and control commands and is evaluated in closed-loop CARLA and Bench2Drive experiments.",
            4,
        ),
        (
            "reason-drive",
            "[演示论文] ReasonDrive: Multimodal Large Language Models for Driving Planning",
            "We introduce a multimodal large language model for autonomous driving scene reasoning and motion planning. "
            "Experiments on nuScenes and NAVSIM benchmark the model against vision-language model baselines.",
            11,
        ),
        (
            "world-vla",
            "[演示论文] WorldVLA: A Driving World Model with Language-Guided Actions",
            "This novel world model is a vision-language-action architecture for self-driving vehicles. "
            "It learns a driving policy and is validated with closed-loop trajectory planning experiments.",
            27,
        ),
        (
            "drive-bench",
            "[演示论文] DriveBench-MLLM: Evaluating Vision-Language Models in Autonomous Vehicles",
            "We introduce a benchmark and open dataset for large vision-language model evaluation in autonomous driving scenarios, including safety and robustness tests.",
            31,
        ),
    ]
    papers: List[RadarItem] = []
    for index, (slug, title, abstract, hours_ago) in enumerate(paper_specs):
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
        passed, item = score_paper(
            item, ["vision-language-action", "closed-loop", "world model"]
        )
        if passed:
            item.tags.insert(0, "DEMO")
            item.is_important = item.score >= 42
            papers.append(item)
    return news + papers


def seed_demo(database: Database) -> int:
    database.initialize()
    items = build_demo_items()
    for item in items:
        database.upsert_item(item)
    return len(items)

