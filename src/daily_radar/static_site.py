from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .config import Settings
from .db import Database
from .exporter import export_markdown, export_rss
from .processing.scoring import news_category_label, paper_category_label
from .time_windows import PeriodWindow, build_period_window


WEB_ROOT = Path(__file__).resolve().parent / "web"
SOURCE_HEALTH_MAX_AGE = timedelta(hours=36)

VERIFICATION_LABELS = {
    "verified-primary": "一手来源已验证",
    "verified-publisher": "媒体来源已验证",
    "verified-community": "社区原帖已验证",
    "verified-link": "外链可访问",
    "access-restricted": "来源限制自动访问",
    "unverified": "未验证",
}

COMPONENT_LABELS = {
    "semantic_relevance": "语义相关性",
    "novelty": "新颖性",
    "ai_relevance": "AI 相关性",
    "impact": "影响力",
    "community_heat": "社区热度",
    "evidence_quality": "证据质量",
    "mllm_vla_relevance": "MLLM/VLA 相关性",
    "driving_relevance": "自动驾驶相关性",
    "method_novelty": "方法新颖性",
    "source": "来源质量",
    "freshness": "新鲜度",
    "multi_source": "多源佐证",
    "community": "社区信号",
    "preference": "个人偏好",
    "marketing_penalty": "营销惩罚",
    "domain_relevance": "双轴相关性",
    "method_contribution": "方法贡献",
    "experimental_evidence": "实验依据",
    "reproducibility": "可复现性",
}


def _as_utc(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_datetime(value: str, timezone_name: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return value or "时间未知"
    return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")


def _category_label(item: Dict[str, Any]) -> str:
    if item.get("kind") == "news":
        return news_category_label(str(item.get("category", "other")))
    return paper_category_label(str(item.get("category", "other")))


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _prepared_community_signals(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for raw in metadata.get("community_signals", []):
        if not isinstance(raw, dict) or not raw.get("platform"):
            continue
        platform = str(raw.get("platform", ""))[:80]
        parts = []
        rank = _nonnegative_int(raw.get("rank"))
        points = _nonnegative_int(raw.get("points"))
        comments = _nonnegative_int(raw.get("comments"))
        views = _nonnegative_int(raw.get("views"))
        likes = _nonnegative_int(raw.get("likes"))
        favorites = _nonnegative_int(raw.get("favorites"))
        if rank:
            parts.append(f"热榜 #{rank}")
        if points:
            parts.append(
                f"{points} points" if platform == "Hacker News" else f"热度 {points}"
            )
        if views:
            parts.append(f"{views} 浏览")
        if likes:
            parts.append(f"{likes} 点赞")
        if comments:
            parts.append(f"{comments} 评论")
        if favorites:
            parts.append(f"{favorites} 收藏")
        prepared.append(
            {
                "platform": platform,
                "metrics": " · ".join(parts) or "平台热榜记录",
                "qualified": raw.get("qualified") is True,
                "url": str(raw.get("discussion_url", "")),
            }
        )
    return prepared[:6]


def _prepare_item(
    item: Dict[str, Any], timezone_name: str, today_urls: set
) -> Dict[str, Any]:
    prepared = dict(item)
    metadata = dict(item.get("metadata") or {})
    provenance = dict(metadata.get("provenance") or {})
    prepared["metadata"] = metadata
    prepared["provenance"] = provenance
    prepared["community_signals"] = _prepared_community_signals(metadata)
    prepared["category_label"] = _category_label(item)
    prepared["published_display"] = _format_datetime(
        str(item.get("published_at", "")), timezone_name
    )
    prepared["arxiv_first_submitted_display"] = _format_datetime(
        str(metadata.get("arxiv_first_submitted_at", "")), timezone_name
    )
    prepared["announcement_batch_display"] = _format_datetime(
        str(metadata.get("announcement_batch_at", "")), timezone_name
    )
    prepared["is_today"] = item.get("canonical_url") in today_urls
    prepared["verification_label"] = VERIFICATION_LABELS.get(
        str(provenance.get("status", "")), str(provenance.get("status", "待验证"))
    )
    searchable = [
        str(item.get("title", "")),
        str(item.get("summary", "")),
        str(metadata.get("summary_zh", "")),
        str(item.get("source_name", "")),
        prepared["category_label"],
        " ".join(str(value) for value in item.get("tags", [])),
        " ".join(str(value) for value in item.get("authors", [])),
        str(metadata.get("arxiv_first_submitted_at", "")),
    ]
    prepared["search_text"] = " ".join(searchable).casefold()
    return prepared


def _window_payload(window: PeriodWindow) -> Dict[str, Optional[str]]:
    return {
        "period": window.period,
        "label": window.label,
        "published_since": (
            window.published_since.isoformat() if window.published_since else None
        ),
        "local_date": window.local_date,
    }


def build_static_site(
    settings: Settings,
    output_dir: Path,
    database: Optional[Database] = None,
    now: Optional[datetime] = None,
    site_url: str = "",
) -> List[Path]:
    """Build a self-contained, read-only GitHub Pages snapshot.

    Only verified and feed-eligible records are published. Papers from previous
    days remain available under the explicit recent view and never fill today's
    section.
    """

    current = _as_utc(now)
    database = database or Database(settings.database_path)
    database.initialize()
    output_dir = Path(output_dir)
    assets_dir = output_dir / "assets"
    data_dir = output_dir / "data"
    assets_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    news_window = build_period_window(
        "news", "recent", settings.timezone, settings.news.lookback_hours, current
    )
    paper_today_window = build_period_window(
        "paper", "today", settings.timezone, settings.papers.lookback_hours, current
    )
    paper_recent_window = build_period_window(
        "paper", "recent", settings.timezone, settings.papers.lookback_hours, current
    )

    news = database.list_items(
        kind="news",
        limit=500,
        verified_only=True,
        published_since=news_window.published_since,
        eligible_only=True,
        prompt_version=settings.llm.prompt_version,
    )
    papers_today = database.list_items(
        kind="paper",
        limit=500,
        verified_only=True,
        published_since=paper_today_window.published_since,
        eligible_only=True,
        prompt_version=settings.llm.prompt_version,
    )
    papers_recent = database.list_items(
        kind="paper",
        limit=500,
        verified_only=True,
        published_since=paper_recent_window.published_since,
        eligible_only=True,
        prompt_version=settings.llm.prompt_version,
    )
    today_urls = {item["canonical_url"] for item in papers_today}

    prepared_news = [
        _prepare_item(item, settings.timezone, today_urls) for item in news
    ]
    prepared_papers = [
        _prepare_item(item, settings.timezone, today_urls) for item in papers_recent
    ]
    source_checks = database.recent_source_checks()
    runs = database.recent_runs(limit=8)
    for source in source_checks:
        checked_at = _parse_datetime(str(source.get("checked_at", "")))
        source["is_stale"] = (
            checked_at is None or current - checked_at.astimezone(timezone.utc) > SOURCE_HEALTH_MAX_AGE
        )
        source["health_class"] = (
            "stale"
            if source["is_stale"]
            else ("healthy" if source.get("success") else "unhealthy")
        )
        source["checked_display"] = _format_datetime(
            str(source.get("checked_at", "")), settings.timezone
        )
    for run in runs:
        run["finished_display"] = _format_datetime(
            str(run.get("finished_at", "")), settings.timezone
        )

    generated_display = current.astimezone(ZoneInfo(settings.timezone)).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    local_date = current.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    llm_usage = database.llm_usage_summary(local_date)
    llm_usage["stages"] = database.llm_usage_breakdown(local_date)
    llm_usage.update(
        {
            "provider": settings.llm.provider,
            "model": settings.llm.model,
            "thinking": "enabled" if settings.llm.thinking_enabled else "disabled",
            "reasoning_effort": settings.llm.reasoning_effort,
            "paper_triage_thinking": "disabled",
            "daily_token_limit": settings.llm.daily_token_limit,
            "daily_cost_limit_usd": settings.llm.daily_cost_limit_usd,
            "prompt_version": settings.llm.prompt_version,
        }
    )
    stats = {
        "total": len(news) + len(papers_today),
        "news": len(news),
        "papers_today": len(papers_today),
        "important": sum(bool(item["is_important"]) for item in news + papers_today),
    }
    healthy_sources = sum(
        bool(source.get("success")) and not bool(source.get("is_stale"))
        for source in source_checks
    )
    stale_sources = sum(bool(source.get("is_stale")) for source in source_checks)

    environment = Environment(
        loader=FileSystemLoader(str(WEB_ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("pages.html")
    html = template.render(
        version=__version__,
        generated_at=current.isoformat(),
        generated_display=generated_display,
        site_url=site_url.rstrip("/") + "/" if site_url else "",
        news=prepared_news,
        papers=prepared_papers,
        stats=stats,
        source_checks=source_checks,
        healthy_sources=healthy_sources,
        stale_sources=stale_sources,
        runs=runs,
        news_window=news_window,
        paper_today_window=paper_today_window,
        paper_recent_window=paper_recent_window,
        verification_labels=VERIFICATION_LABELS,
        component_labels=COMPONENT_LABELS,
        llm_usage=llm_usage,
    )

    index_path = output_dir / "index.html"
    json_path = data_dir / "latest.json"
    markdown_path = output_dir / "daily.md"
    rss_path = output_dir / "feed.xml"
    css_path = assets_dir / "pages.css"
    js_path = assets_dir / "pages.js"
    nojekyll_path = output_dir / ".nojekyll"

    index_path.write_text(html, encoding="utf-8")
    payload = {
        "version": __version__,
        "generated_at": current.isoformat(),
        "timezone": settings.timezone,
        "site_url": site_url,
        "windows": {
            "news": _window_payload(news_window),
            "papers_today": _window_payload(paper_today_window),
            "papers_recent": _window_payload(paper_recent_window),
        },
        "counts": stats,
        "llm_usage": llm_usage,
        "news": news,
        "papers_today": papers_today,
        "papers_recent": papers_recent,
        "source_checks": source_checks,
        "source_health": {
            "healthy_fresh": healthy_sources,
            "stale": stale_sources,
            "total": len(source_checks),
            "max_age_hours": int(SOURCE_HEALTH_MAX_AGE.total_seconds() / 3600),
        },
        "runs": runs,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    daily_items = sorted(
        news + papers_today,
        key=lambda item: str(item.get("published_at", "")),
        reverse=True,
    )
    export_markdown(daily_items, markdown_path, generated_at=current)
    export_rss(
        daily_items,
        rss_path,
        channel_link=site_url or "http://127.0.0.1:8000/",
        generated_at=current,
    )
    shutil.copyfile(WEB_ROOT / "static" / "pages.css", css_path)
    shutil.copyfile(WEB_ROOT / "static" / "pages.js", js_path)
    nojekyll_path.write_text("", encoding="utf-8")

    return [
        index_path,
        json_path,
        markdown_path,
        rss_path,
        css_path,
        js_path,
        nojekyll_path,
    ]
