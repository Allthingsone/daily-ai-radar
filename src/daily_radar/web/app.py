from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import load_settings
from ..db import Database
from ..processing.scoring import news_category_label, paper_category_label
from ..time_windows import PeriodWindow, build_period_window


WEB_ROOT = Path(__file__).resolve().parent
COMPONENT_MAX = {
    "relevance": 25,
    "impact": 25,
    "source": 15,
    "novelty": 10,
    "corroboration": 10,
    "momentum": 10,
    "personal": 5,
    "penalty": 20,
    "domain_relevance": 35,
    "contribution": 20,
    "evidence": 15,
    "reproducibility": 10,
    "recency": 10,
    "community": 5,
}

COMPONENT_LABELS = {
    "relevance": "AI 相关性",
    "impact": "影响力",
    "source": "来源可信度",
    "novelty": "新鲜度",
    "corroboration": "多源佐证",
    "momentum": "社区热度",
    "personal": "个人偏好",
    "penalty": "降权",
    "domain_relevance": "领域相关性",
    "contribution": "方法贡献",
    "evidence": "实验依据",
    "reproducibility": "可复现性",
    "recency": "新鲜度",
    "community": "社区信号",
}

VERIFICATION_LABELS = {
    "verified-primary": "一手来源已验证",
    "verified-publisher": "发布域名已验证",
    "verified-link": "外链可访问",
    "access-restricted": "站点限制自动访问",
    "unverified": "暂未验证",
    "domain-mismatch": "域名不匹配",
    "invalid-record": "记录不一致",
}


def create_app(config_path: str = "") -> FastAPI:
    settings = load_settings(config_path)
    database = Database(settings.database_path)
    database.initialize()
    demo_mode = settings.database_path.name == "demo_radar.db"
    templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
    timezone = ZoneInfo(settings.timezone)

    def format_datetime(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
        except (ValueError, AttributeError):
            return value

    def resolve_window(kind: str, period: str) -> PeriodWindow:
        lookback_hours = (
            settings.news.lookback_hours
            if kind == "news"
            else settings.papers.lookback_hours
        )
        return build_period_window(
            kind,
            period,
            settings.timezone,
            lookback_hours,
        )

    def category_label(item: Dict[str, Any]) -> str:
        return (
            news_category_label(item["category"])
            if item["kind"] == "news"
            else paper_category_label(item["category"])
        )

    templates.env.filters["localtime"] = format_datetime
    templates.env.filters["category_label"] = category_label

    app = FastAPI(
        title="Daily AI Radar",
        description="AI news and MLLM/VLA-for-autonomous-driving paper radar",
        version=__version__,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(WEB_ROOT / "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request,
        kind: str = Query("news", pattern="^(news|paper)$"),
        view: str = Query("important", pattern="^(important|all)$"),
        period: str = Query("auto", pattern="^(auto|today|recent|all)$"),
        category: str = "",
        q: str = "",
    ) -> HTMLResponse:
        window = resolve_window(kind, period)
        recent_window = resolve_window(kind, "recent")
        items = database.list_items(
            kind=kind,
            important_only=view == "important",
            category=category,
            query=q.strip(),
            limit=200,
            verified_only=not demo_mode,
            published_since=window.published_since,
            eligible_only=not demo_mode,
        )
        kind_items = database.list_items(
            kind=kind,
            limit=500,
            verified_only=not demo_mode,
            published_since=window.published_since,
            eligible_only=not demo_mode,
        )
        categories = sorted({item["category"] for item in kind_items})
        source_checks = database.recent_source_checks()
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "items": items,
                "kind": kind,
                "view": view,
                "period": window.period,
                "period_label": window.label,
                "recent_period_label": recent_window.label,
                "local_date": window.local_date,
                "category": category,
                "query": q,
                "categories": categories,
                "stats": database.stats(
                    verified_only=not demo_mode,
                    published_since=window.published_since,
                    eligible_only=not demo_mode,
                ),
                "runs": database.recent_runs(6),
                "source_checks": source_checks,
                "healthy_sources": sum(check["success"] for check in source_checks),
                "component_max": COMPONENT_MAX,
                "component_labels": COMPONENT_LABELS,
                "verification_labels": VERIFICATION_LABELS,
                "has_demo": any(item.get("metadata", {}).get("demo") for item in items),
                "version": __version__,
            },
        )

    @app.get("/api/items")
    async def api_items(
        kind: str = Query("news", pattern="^(news|paper)$"),
        important: bool = False,
        period: str = Query("auto", pattern="^(auto|today|recent|all)$"),
        category: str = "",
        q: str = "",
        limit: int = Query(100, ge=1, le=500),
        verified: bool = True,
    ) -> JSONResponse:
        window = resolve_window(kind, period)
        items = database.list_items(
            kind,
            important,
            category,
            q,
            limit,
            verified_only=verified and not demo_mode,
            published_since=window.published_since,
            eligible_only=verified and not demo_mode,
        )
        return JSONResponse(
            {
                "count": len(items),
                "period": window.period,
                "period_label": window.label,
                "published_since": (
                    window.published_since.isoformat()
                    if window.published_since is not None
                    else None
                ),
                "items": items,
            }
        )

    @app.get("/api/runs")
    async def api_runs() -> JSONResponse:
        return JSONResponse({"runs": database.recent_runs(30)})

    @app.get("/api/sources")
    async def api_sources() -> JSONResponse:
        checks = database.recent_source_checks()
        return JSONResponse({"count": len(checks), "sources": checks})

    @app.post("/api/items/{item_id}/feedback")
    async def feedback(item_id: int, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Expected JSON body") from exc
        value = str(payload.get("value", ""))
        note = str(payload.get("note", ""))
        try:
            database.record_feedback(item_id, value, note)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "item_id": item_id, "value": value})

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "stats": database.stats(verified_only=True, eligible_only=True),
            "sources": database.recent_source_checks(),
        }

    return app
