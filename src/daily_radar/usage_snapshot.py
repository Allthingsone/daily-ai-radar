from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database


def restore_usage_snapshot(
    database: Database,
    settings: Settings,
    url: str,
    now: Optional[datetime] = None,
    opener: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Carry today's public usage totals into a fresh Actions workspace.

    GitHub-hosted runners start with an empty SQLite database. Restoring the
    already-published aggregate prevents a second manual run on the same day
    from resetting the project's local token/cost cap. No prompt or secret is
    downloaded or persisted.
    """

    if urlsplit(url).scheme != "https":
        return {"restored": False, "reason": "snapshot URL must use HTTPS"}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = current.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    request = Request(url, headers={"User-Agent": "DailyAIRadar/0.8.1"})
    try:
        with (opener or urlopen)(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return {"restored": False, "reason": f"snapshot HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"restored": False, "reason": "snapshot unavailable"}

    remote = payload.get("llm_usage") if isinstance(payload, dict) else None
    if not isinstance(remote, dict):
        return {"restored": False, "reason": "snapshot has no LLM usage"}
    if remote.get("local_date") != local_date:
        return {"restored": False, "reason": "snapshot belongs to another day"}
    if remote.get("model") not in {None, "", settings.llm.model}:
        return {"restored": False, "reason": "snapshot model differs"}

    existing = database.llm_usage_summary(local_date)
    remote_total = int(remote.get("total_tokens", 0) or 0)
    remote_cost = float(remote.get("estimated_cost_usd", 0) or 0)
    if remote_total <= existing["total_tokens"] and remote_cost <= existing["estimated_cost_usd"]:
        return {"restored": False, "reason": "local usage is already current"}

    integer_fields = (
        "request_items",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "total_tokens",
    )
    record: Dict[str, Any] = {
        "occurred_at": current.astimezone(timezone.utc).isoformat(),
        "local_date": local_date,
        "provider": "deepseek",
        "model": settings.llm.model,
        "purpose": "github-pages-usage-carry-forward",
        "call_count": max(1, int(remote.get("calls", 0) or 0) - existing["calls"]),
        "estimated_cost_usd": max(
            0.0, remote_cost - float(existing["estimated_cost_usd"])
        ),
        "pricing_tier": "carried",
        "prompt_version": str(remote.get("prompt_version", "")),
        "status": "success",
    }
    for field in integer_fields:
        record[field] = max(
            0, int(remote.get(field, 0) or 0) - int(existing.get(field, 0) or 0)
        )
    database.record_llm_usage(record)
    return {
        "restored": True,
        "local_date": local_date,
        "tokens": record["total_tokens"],
        "estimated_cost_usd": round(record["estimated_cost_usd"], 6),
    }
