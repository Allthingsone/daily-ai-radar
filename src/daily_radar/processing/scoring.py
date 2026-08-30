from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple

from ..eligibility import NEWS_GATE_RULE_VERSION
from ..models import RadarItem
from .normalize import unique_preserving_order


NEWS_AI_TERMS: Sequence[Tuple[str, float]] = (
    ("artificial intelligence", 5),
    ("machine learning", 3),
    ("deep learning", 4),
    ("computer vision", 4),
    ("reinforcement learning", 4),
    ("robotics", 3),
    ("large language model", 6),
    ("multimodal", 5),
    ("vision-language", 6),
    ("vision language", 5),
    ("generative ai", 5),
    ("foundation model", 5),
    ("reasoning model", 5),
    ("diffusion model", 5),
    ("generative model", 5),
    ("speech model", 5),
    ("video model", 5),
    ("coding model", 5),
    ("ai model", 5),
    ("ai system", 4),
    ("ai agent", 5),
    ("agentic", 4),
    ("llm", 5),
    ("mllm", 6),
    ("vlm", 5),
    ("vla", 6),
    ("chatgpt", 5),
    ("claude", 4),
    ("gemini", 4),
    ("llama", 4),
    ("deepseek", 4),
    ("openai", 3),
    ("meta ai", 4),
    ("hugging face", 2),
    ("transformer", 3),
    ("neural network", 3),
    ("autonomous driving", 6),
)

NEWS_IMPACT_TERMS: Sequence[Tuple[str, float]] = (
    ("launch", 4),
    ("release", 4),
    ("announc", 3),
    ("unveil", 4),
    ("introduc", 3),
    ("open source", 5),
    ("open-source", 5),
    ("state of the art", 5),
    ("sota", 4),
    ("benchmark", 3),
    ("new model", 5),
    ("breakthrough", 5),
    ("outperform", 4),
    ("achiev", 3),
    ("discover", 4),
    ("demonstrat", 3),
    ("deprecat", 4),
    ("production-ready", 3),
    ("api", 2),
)

NEWS_RELEASE_SIGNALS: Sequence[Tuple[str, str]] = (
    ("releas", "发布"),
    ("launch", "上线"),
    ("unveil", "亮相"),
    ("introduc", "推出"),
    ("announc", "正式公布"),
    ("roll out", "开始推送"),
    ("debut", "首次亮相"),
    ("now available", "现已可用"),
    ("general availability", "正式可用"),
    ("open source", "开放源码"),
    ("open-source", "开放源码"),
    ("publish", "公开发布"),
    ("ships", "正式交付"),
    ("shipping", "正式交付"),
)

NEWS_RESULT_SIGNALS: Sequence[Tuple[str, str]] = (
    ("study finds", "研究发现"),
    ("study shows", "研究表明"),
    ("research finds", "研究发现"),
    ("researchers", "研究团队成果"),
    ("first", "首次取得结果"),
    ("finds", "发现新结果"),
    ("shows", "展示新结果"),
    ("reveals", "揭示新结果"),
    ("demonstrat", "完成验证"),
    ("achiev", "取得新结果"),
    ("outperform", "性能超越"),
    ("state of the art", "达到最新水平"),
    ("state-of-the-art", "达到最新水平"),
    ("breakthrough", "技术突破"),
    ("discover", "新发现"),
    ("solv", "解决问题"),
    ("crack", "解决难题"),
    ("proves", "完成证明"),
    ("built", "构建出原型"),
    ("developed", "研发出新方法"),
    ("generat", "生成能力验证"),
    ("turns", "能力转化验证"),
    ("pushes", "能力推进验证"),
    ("beats", "对比取得领先"),
    ("tops", "排名取得领先"),
    ("improves", "取得性能改进"),
    ("can now", "展示新能力"),
    ("record", "刷新纪录"),
)

NEWS_ARTIFACT_SIGNALS: Sequence[Tuple[str, str]] = (
    ("foundation model", "基础模型"),
    ("reasoning model", "推理模型"),
    ("language model", "语言模型"),
    ("vision model", "视觉模型"),
    ("world model", "世界模型"),
    ("model", "模型"),
    ("models", "模型"),
    ("gpt", "GPT 模型"),
    ("claude", "Claude 模型"),
    ("gemini", "Gemini 模型"),
    ("llama", "Llama 模型"),
    ("deepseek", "DeepSeek 模型"),
    ("qwen", "Qwen 模型"),
    ("mistral", "Mistral 模型"),
    ("grok", "Grok 模型"),
    ("gemma", "Gemma 模型"),
    ("checkpoint", "模型权重"),
    ("checkpoints", "模型权重"),
    ("weights", "模型权重"),
    ("agent", "智能体"),
    ("agents", "智能体"),
    ("api", "API"),
    ("sdk", "SDK"),
    ("framework", "框架"),
    ("frameworks", "框架"),
    ("library", "开发库"),
    ("libraries", "开发库"),
    ("runtime", "运行时"),
    ("inference engine", "推理引擎"),
    ("tool", "工具"),
    ("tools", "工具"),
    ("platform", "平台"),
    ("dataset", "数据集"),
    ("datasets", "数据集"),
    ("benchmark", "基准"),
    ("benchmarks", "基准"),
    ("evaluation suite", "评测套件"),
    ("chip", "AI 芯片"),
    ("chips", "AI 芯片"),
    ("accelerator", "加速器"),
    ("robot", "机器人"),
    ("robots", "机器人"),
    ("prototype", "技术原型"),
    ("prototypes", "技术原型"),
    ("architecture", "新架构"),
    ("method", "新方法"),
    ("methods", "新方法"),
    ("technique", "新技术"),
    ("techniques", "新技术"),
    ("algorithm", "算法"),
    ("algorithms", "算法"),
    ("feature", "新功能"),
    ("features", "新功能"),
)

NEWS_RESEARCH_EVIDENCE: Sequence[Tuple[str, str]] = (
    ("study", "研究"),
    ("research", "研究"),
    ("paper", "论文"),
    ("experiment", "实验"),
    ("evaluation", "评测"),
    ("benchmark", "基准测试"),
    ("result", "实验结果"),
    ("finding", "研究发现"),
    ("prototype", "技术原型"),
    ("proof", "技术证明"),
    ("theorem", "理论成果"),
    ("security flaw", "安全研究结果"),
    ("vulnerability", "漏洞研究"),
    ("math problem", "数学问题成果"),
)

NEWS_STRONG_EVENT_SIGNALS: Sequence[Tuple[str, str]] = (
    ("new model", "新模型"),
    ("open-source model", "开源模型"),
    ("open source model", "开源模型"),
    ("model is now available", "模型正式可用"),
    ("weights are available", "权重正式开放"),
    ("dataset released", "数据集发布"),
    ("benchmark released", "基准发布"),
    ("general availability", "正式可用"),
    ("state-of-the-art", "最新研究成果"),
    ("breakthrough", "技术突破"),
)

NEWS_NON_EVENT_SIGNALS: Sequence[Tuple[str, str]] = (
    ("opinion", "观点评论"),
    ("debate", "观点争论"),
    ("interview", "人物访谈"),
    ("podcast", "播客内容"),
    ("lawsuit", "诉讼事件"),
    ("court", "司法事件"),
    ("judge", "司法事件"),
    ("regulation", "监管动态"),
    ("policy", "政策动态"),
    ("funding", "融资动态"),
    ("valuation", "估值动态"),
    ("acquisition", "并购动态"),
    ("revenue", "财务动态"),
    ("earnings", "财务动态"),
    ("parenting", "使用观点"),
    ("usage is", "使用观点"),
    ("content flood", "内容生态讨论"),
)

NEWS_HARD_EXCLUSION_SIGNALS: Sequence[Tuple[str, str]] = (
    ("sponsored", "赞助内容"),
    ("presented by", "商业合作内容"),
    ("paid content", "付费内容"),
    ("partner content", "合作推广内容"),
    ("advertorial", "广告软文"),
    ("register for the webinar", "活动推广"),
)

MARKETING_TERMS = (
    "sponsored",
    "webinar",
    "buy now",
    "limited offer",
    "ultimate guide",
    "register today",
    "weekly roundup",
)

PAPER_MODEL_TERMS: Sequence[Tuple[str, float]] = (
    ("vision-language-action", 3.5),
    ("vision language action", 3.5),
    ("vla", 3.2),
    ("multimodal large language model", 3.0),
    ("multi-modal large language model", 3.0),
    ("mllm", 3.0),
    ("large vision-language model", 2.8),
    ("large vision language model", 2.8),
    ("vision-language model", 2.4),
    ("vision language model", 2.4),
    ("vlm", 2.4),
    ("multimodal language model", 2.2),
    ("multi-modal language model", 2.2),
    ("language-guided", 1.5),
    ("language conditioned", 1.5),
    ("multimodal reasoning", 1.8),
)

PAPER_DRIVING_TERMS: Sequence[Tuple[str, float]] = (
    ("autonomous driving", 3.5),
    ("self-driving", 3.5),
    ("autonomous vehicle", 3.0),
    ("driving agent", 3.2),
    ("end-to-end driving", 3.2),
    ("end to end driving", 3.2),
    ("driving policy", 2.5),
    ("driving scene", 2.0),
    ("driving scenario", 2.0),
    ("driving behavior", 2.0),
    ("motion planning", 1.0),
    ("trajectory planning", 1.2),
    ("trajectory prediction", 1.0),
    ("closed-loop driving", 2.5),
    ("carla", 2.0),
    ("navsim", 2.2),
    ("bench2drive", 2.2),
    ("nuplan", 2.0),
    ("nuscenes", 2.0),
    ("waymo open", 2.0),
    ("bdd100k", 1.8),
)


def _contains(text: str, term: str) -> bool:
    if len(term) <= 5 and term.replace("-", "").isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def _matches(text: str, terms: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    lowered = text.lower()
    return [(term, weight) for term, weight in terms if _contains(lowered, term)]


def _named_matches(
    text: str, terms: Sequence[Tuple[str, str]]
) -> List[str]:
    lowered = text.lower()
    return unique_preserving_order(
        label for term, label in terms if _contains(lowered, term)
    )


def news_event_gate(item: RadarItem) -> Tuple[bool, Dict[str, object]]:
    """Require a concrete AI release or a verifiable technical result event."""

    title = item.title.lower()
    text = f"{item.title} {item.summary}".lower()
    release_signals = _named_matches(text, NEWS_RELEASE_SIGNALS)
    result_signals = _named_matches(text, NEWS_RESULT_SIGNALS)
    artifacts = _named_matches(text, NEWS_ARTIFACT_SIGNALS)
    research_evidence = _named_matches(text, NEWS_RESEARCH_EVIDENCE)
    strong_signals = _named_matches(text, NEWS_STRONG_EVENT_SIGNALS)
    non_event_signals = _named_matches(text, NEWS_NON_EVENT_SIGNALS)
    hard_exclusions = _named_matches(text, NEWS_HARD_EXCLUSION_SIGNALS)
    ai_subject_terms = [term for term, _ in _matches(text, NEWS_AI_TERMS)]
    title_release_signals = _named_matches(title, NEWS_RELEASE_SIGNALS)
    title_result_signals = _named_matches(title, NEWS_RESULT_SIGNALS)
    title_artifacts = _named_matches(title, NEWS_ARTIFACT_SIGNALS)
    title_research_evidence = _named_matches(title, NEWS_RESEARCH_EVIDENCE)
    title_strong_signals = _named_matches(title, NEWS_STRONG_EVENT_SIGNALS)
    title_non_event_signals = _named_matches(title, NEWS_NON_EVENT_SIGNALS)
    title_ai_subject_terms = [term for term, _ in _matches(title, NEWS_AI_TERMS)]

    github_release = item.source_type == "github-release"
    trusted_source_tags = {
        "model-release",
        "research",
        "open-source",
        "developer-tools",
        "agents",
        "inference",
        "hardware",
        "autonomous-driving",
    }
    trusted_ai_source_context = github_release or (
        item.source_tier == 1
        and item.source_focus >= 0.85
        and bool(trusted_source_tags.intersection(item.tags))
    )
    ai_subject_passed = bool(ai_subject_terms) or trusted_ai_source_context
    release_event = bool(release_signals and artifacts)
    result_event = bool(result_signals and research_evidence)
    strong_event = bool(strong_signals and (artifacts or research_evidence))
    title_release_event = bool(
        title_release_signals and (title_artifacts or title_ai_subject_terms)
    )
    title_result_event = bool(
        title_result_signals and (title_artifacts or title_research_evidence)
    )
    title_strong_event = bool(
        title_strong_signals and (title_artifacts or title_research_evidence)
    )
    title_anchored_event = (
        title_release_event or title_result_event or title_strong_event
    )
    full_event = github_release or release_event or result_event or strong_event
    requires_title_anchor = item.source_type in {"media", "community"}
    passed = ai_subject_passed and (
        title_anchored_event if requires_title_anchor else full_event
    )

    # Business, policy and opinion stories do not qualify merely because they
    # use words such as "announce" or "model". A strong release phrase or
    # explicit research-result evidence can still override this guard.
    if (
        title_non_event_signals
        and not github_release
        and not title_strong_event
        and not title_result_event
    ):
        passed = False
    if hard_exclusions:
        passed = False

    title_mentions_model = bool(re.search(r"\bmodels?\b", title)) or any(
        term in title
        for term in (
            "gpt",
            "claude",
            "gemini",
            "llama",
            "deepseek",
            "qwen",
            "mistral",
            "grok",
            "gemma",
        )
    )
    title_mentions_open_tool = any(
        term in title
        for term in (
            "open framework",
            "open-source",
            "open source",
            "sdk",
            "library",
            "runtime",
            "inference engine",
        )
    )

    if result_event and not release_event:
        event_type = "research-result"
    elif title_release_event and any(
        term in title for term in ("dataset", "benchmark", "evaluation suite")
    ):
        event_type = "dataset-benchmark"
    elif title_mentions_model:
        event_type = "model-release"
    elif title_mentions_open_tool:
        event_type = "open-source-tool"
    elif any(
        term in text
        for term in (
            "foundation model",
            "reasoning model",
            "language model",
            "vision model",
            "world model",
            "new model",
            "model release",
            "model launched",
            "model",
            "gpt",
            "claude",
            "gemini",
            "llama",
            "deepseek",
            "qwen",
            "mistral",
            "grok",
            "gemma",
        )
    ):
        event_type = "model-release"
    elif any(
        term in text
        for term in (
            "open source",
            "open-source",
            "github",
            "sdk",
            "framework",
            "library",
            "runtime",
            "inference engine",
        )
    ):
        event_type = "open-source-tool"
    elif any(term in text for term in ("chip", "accelerator", "robot", "hardware")):
        event_type = "hardware-robotics"
    else:
        event_type = "product-tool-release"

    event_score = min(
        10.0,
        (2.0 if github_release else 0.0)
        + len(release_signals) * 1.5
        + len(result_signals) * 1.5
        + len(strong_signals) * 2.0
        + min(2.0, len(artifacts) * 0.5)
        + min(2.0, len(research_evidence) * 0.5),
    )
    evidence: Dict[str, object] = {
        "rule_version": NEWS_GATE_RULE_VERSION,
        "passed": passed,
        "event_type": event_type if passed else "not-a-release-or-result",
        "event_score": round(event_score, 2),
        "release_signals": release_signals,
        "result_signals": result_signals,
        "artifacts": artifacts,
        "research_evidence": research_evidence,
        "strong_signals": strong_signals,
        "non_event_signals": non_event_signals,
        "hard_exclusions": hard_exclusions,
        "ai_subject_passed": ai_subject_passed,
        "ai_subject_terms": ai_subject_terms,
        "trusted_ai_source_context": trusted_ai_source_context,
        "title_anchored_event": title_anchored_event,
        "title_release_signals": title_release_signals,
        "title_result_signals": title_result_signals,
    }
    return passed, evidence


def _freshness_score(published_at: datetime, now: datetime, maximum: float) -> float:
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
    if age_hours <= 12:
        fraction = 1.0
    elif age_hours <= 24:
        fraction = 0.85
    elif age_hours <= 48:
        fraction = 0.65
    elif age_hours <= 96:
        fraction = 0.4
    else:
        fraction = 0.2
    return round(maximum * fraction, 2)


def _keyword_score(text: str, keywords: Iterable[str], maximum: float) -> Tuple[float, List[str]]:
    lowered = text.lower()
    matched = [keyword for keyword in keywords if keyword.lower() in lowered]
    if not matched:
        return 0.0, []
    return min(maximum, 2.0 + len(matched) * 1.5), matched


def classify_news(text: str, event_type: str = "") -> str:
    if event_type and event_type != "not-a-release-or-result":
        return event_type
    lowered = text.lower()
    if any(term in lowered for term in ("regulation", "policy", "safety", "governance")):
        return "safety-policy"
    if any(term in lowered for term in ("github", "sdk", "api", "developer", "agentic", "agent")):
        return "agents-devtools"
    if any(term in lowered for term in ("funding", "acquisition", "revenue", "enterprise")):
        return "industry"
    if any(term in lowered for term in ("paper", "research", "benchmark", "study")):
        return "research"
    if any(term in lowered for term in ("launch", "release", "model", "preview")):
        return "model-release"
    return "ai-general"


def score_news(
    item: RadarItem,
    personal_keywords: Iterable[str] = (),
    now: datetime = None,
) -> RadarItem:
    now = now or datetime.now(timezone.utc)
    text = f"{item.title} {item.summary}"
    title_lower = item.title.lower()
    ai_matches = _matches(text, NEWS_AI_TERMS)
    impact_matches = _matches(text, NEWS_IMPACT_TERMS)
    event_passed, event_evidence = news_event_gate(item)
    item.metadata["news_gate"] = event_evidence

    relevance = min(25.0, item.source_focus * 10.0 + sum(weight for _, weight in ai_matches))
    impact = min(
        25.0,
        (3.0 if item.source_type in {"official", "github-release"} else 0.0)
        + sum(
            weight * (1.4 if term in title_lower else 1.0)
            for term, weight in impact_matches
        )
        + float(event_evidence["event_score"]),
    )
    source = {1: 15.0, 2: 11.0, 3: 7.0}.get(item.source_tier, 5.0)
    novelty = _freshness_score(item.published_at, now, 10.0)
    source_count = int(item.metadata.get("source_count", 1))
    corroboration = min(10.0, max(0, source_count - 1) * 4.0)
    if source_count > 1 and item.source_tier == 1:
        corroboration = min(10.0, corroboration + 2.0)
    points = float(item.metadata.get("points", 0) or 0)
    momentum = min(10.0, math.log2(points + 1) * 1.4) if points else 0.0
    if item.source_type == "github-release":
        momentum = max(momentum, 2.0)
    personal, personal_matches = _keyword_score(text, personal_keywords, 5.0)
    marketing_matches = [term for term in MARKETING_TERMS if term in text.lower()]
    penalty = min(20.0, len(marketing_matches) * 6.0)

    components = {
        "relevance": round(relevance, 2),
        "impact": round(impact, 2),
        "source": round(source, 2),
        "novelty": round(novelty, 2),
        "corroboration": round(corroboration, 2),
        "momentum": round(momentum, 2),
        "personal": round(personal, 2),
        "penalty": round(-penalty, 2),
    }
    item.score = round(sum(components.values()), 2)
    item.component_scores = components
    item.category = classify_news(text, str(event_evidence["event_type"]))
    matched_terms = unique_preserving_order(term for term, _ in ai_matches)
    item.tags = unique_preserving_order(item.tags + matched_terms[:5] + personal_matches)
    reasons: List[str] = []
    if event_passed:
        concrete_signals = unique_preserving_order(
            list(event_evidence["strong_signals"])
            + list(event_evidence["release_signals"])
            + list(event_evidence["result_signals"])
        )
        artifacts = list(event_evidence["artifacts"])
        event_label = news_category_label(item.category)
        detail = "、".join((concrete_signals + artifacts)[:5])
        reasons.append(
            f"发布/成果门槛通过：{event_label}"
            + (f"（{detail}）" if detail else "")
        )
    if matched_terms:
        reasons.append("AI 主题命中：" + "、".join(matched_terms[:4]))
    if impact_matches:
        reasons.append("重要性信号：" + "、".join(term for term, _ in impact_matches[:4]))
    reasons.append(f"来源等级 T{item.source_tier}：{item.source_name}")
    if source_count > 1:
        reasons.append(f"发现 {source_count} 个独立来源报道同一事件")
    if marketing_matches:
        reasons.append("检测到营销型措辞，已执行降权")
    provenance = item.metadata.get("provenance", {})
    if provenance.get("status") == "verified-primary":
        reasons.append("来源验证：链接可访问，且匹配一手来源域名")
    elif provenance.get("status") == "verified-publisher":
        reasons.append("来源验证：链接可访问，且匹配发布媒体域名")
    elif provenance.get("status") == "verified-link":
        reasons.append("链接可访问；该条目由社区发现源提供")
    elif provenance.get("status") == "access-restricted":
        reasons.append("来源域名匹配，但站点限制自动访问")
    item.reasons = reasons
    item.metadata["summary_zh"] = (
        f"来自 {item.source_name} 的{news_category_label(item.category)}事件。"
        + (f"主要命中：{'、'.join(matched_terms[:3])}。" if matched_terms else "")
    )
    return item


def paper_gate(item: RadarItem) -> Tuple[bool, Dict[str, object]]:
    text = f"{item.title} {item.summary}".lower()
    model_matches = _matches(text, PAPER_MODEL_TERMS)
    driving_matches = _matches(text, PAPER_DRIVING_TERMS)
    model_score = sum(weight for _, weight in model_matches)
    driving_score = sum(weight for _, weight in driving_matches)
    passed = model_score >= 1.5 and driving_score >= 2.0
    return passed, {
        "model_score": round(model_score, 2),
        "driving_score": round(driving_score, 2),
        "model_terms": [term for term, _ in model_matches],
        "driving_terms": [term for term, _ in driving_matches],
    }


def classify_paper(text: str, title: str = "") -> str:
    lowered = text.lower()
    title_lower = title.lower()
    if "world model" in lowered or "world-model" in lowered:
        return "world-model"
    if any(term in lowered for term in ("vision-language-action", "vision language action", "control command", "driving action")):
        return "end-to-end-vla"
    if (
        "reason" in title_lower
        or any(term in lowered for term in ("chain-of-thought", "dual-system", "system 2"))
    ):
        return "reasoning-vla"
    if any(
        term in title_lower
        for term in ("benchmark", "dataset", "evaluating", "evaluation suite")
    ) or any(
        term in lowered
        for term in (
            "we introduce a benchmark",
            "we present a benchmark",
            "new benchmark",
            "open dataset",
            "evaluation suite",
        )
    ):
        return "data-benchmark"
    if any(
        term in title_lower
        for term in ("safety", "safe", "robust", "uncertainty", "efficient")
    ) or any(
        term in lowered
        for term in ("safety framework", "safety evaluation", "robustness evaluation")
    ):
        return "safety-efficiency"
    return "mllm-vlm-for-ad"


def score_paper(
    item: RadarItem,
    personal_keywords: Iterable[str] = (),
    now: datetime = None,
) -> Tuple[bool, RadarItem]:
    now = now or datetime.now(timezone.utc)
    passed, gate = paper_gate(item)
    item.metadata["gate"] = gate
    if not passed:
        return False, item

    text = f"{item.title} {item.summary}".lower()
    relevance = min(
        35.0,
        8.0 + float(gate["model_score"]) * 3.0 + float(gate["driving_score"]) * 3.0,
    )
    contribution_terms = [
        term
        for term in (
            "we propose",
            "we introduce",
            "novel",
            "framework",
            "architecture",
            "method",
            "world model",
            "diffusion",
        )
        if term in text
    ]
    contribution = min(20.0, 5.0 + len(contribution_terms) * 2.5)
    evidence_terms = [
        term
        for term in (
            "closed-loop",
            "open-loop",
            "benchmark",
            "experiments",
            "outperform",
            "carla",
            "navsim",
            "bench2drive",
            "nuplan",
            "nuscenes",
            "waymo",
        )
        if term in text
    ]
    evidence = min(15.0, len(evidence_terms) * 2.0 + (3.0 if "closed-loop" in text else 0.0))
    reproduction = 0.0
    if "github.com" in text or item.metadata.get("code_url"):
        reproduction += 7.0
    if any(term in text for term in ("code is available", "source code", "project page")):
        reproduction += 2.0
    if any(term in text for term in ("dataset is available", "open dataset")):
        reproduction += 1.0
    reproduction = min(10.0, reproduction)
    recency = _freshness_score(item.published_at, now, 10.0)
    upvotes = float(item.metadata.get("upvotes", 0) or 0)
    community = min(5.0, math.log2(upvotes + 1)) if upvotes else 0.0
    personal, personal_matches = _keyword_score(text, personal_keywords, 5.0)

    components = {
        "domain_relevance": round(relevance, 2),
        "contribution": round(contribution, 2),
        "evidence": round(evidence, 2),
        "reproducibility": round(reproduction, 2),
        "recency": round(recency, 2),
        "community": round(community, 2),
        "personal": round(personal, 2),
    }
    item.score = round(sum(components.values()), 2)
    item.component_scores = components
    item.category = classify_paper(text, item.title)
    item.tags = unique_preserving_order(
        list(gate["model_terms"])[:4]
        + list(gate["driving_terms"])[:4]
        + personal_matches
    )
    item.reasons = [
        "模型轴命中：" + "、".join(list(gate["model_terms"])[:4]),
        "自动驾驶轴命中：" + "、".join(list(gate["driving_terms"])[:4]),
    ]
    provenance = item.metadata.get("provenance", {})
    if provenance.get("status") == "verified-primary":
        item.reasons.append("身份验证：arXiv API ID 与官方摘要页一致")
    if evidence_terms:
        item.reasons.append("实验信号：" + "、".join(evidence_terms[:5]))
    if reproduction:
        item.reasons.append("摘要或元数据中发现代码/数据开放信号")
    item.metadata["summary_zh"] = (
        "论文同时通过多模态模型轴与自动驾驶应用轴筛选，"
        f"归类为“{paper_category_label(item.category)}”。"
    )
    return True, item


def news_category_label(value: str) -> str:
    return {
        "model-release": "模型发布",
        "research-result": "研究成果",
        "dataset-benchmark": "数据集/基准发布",
        "open-source-tool": "开源项目/工具发布",
        "product-tool-release": "产品/API 发布",
        "hardware-robotics": "AI 硬件/机器人成果",
        "research": "研究成果",
        "agents-devtools": "Agent/开发工具",
        "industry": "产业",
        "safety-policy": "安全与政策",
        "ai-general": "AI 综合",
    }.get(value, value)


def paper_category_label(value: str) -> str:
    return {
        "vla-policy": "VLA 驾驶策略",
        "mllm-reasoning": "MLLM 驾驶推理",
        "perception-understanding": "多模态感知与理解",
        "planning": "语言/多模态规划",
        "benchmark-dataset": "数据集与评测",
        "other": "其他双轴论文",
        "end-to-end-vla": "端到端 VLA",
        "reasoning-vla": "推理增强 VLA",
        "world-model": "世界模型",
        "data-benchmark": "数据集与评测",
        "safety-efficiency": "安全与效率",
        "mllm-vlm-for-ad": "MLLM/VLM for AD",
    }.get(value, value)
