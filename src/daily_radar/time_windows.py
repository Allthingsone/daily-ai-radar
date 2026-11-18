from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import ceil
from typing import Optional
from zoneinfo import ZoneInfo


PAPER_COLLECTION_VERSION = "dated-announcement-v1"


def paper_reference(reference: datetime, timezone_name: str, lag_days: int = 1) -> datetime:
    """Resolve the paper batch independently of the digest and billing dates."""
    if not lag_days:
        return reference
    local_timezone = ZoneInfo(timezone_name)
    day = reference.astimezone(local_timezone).date() - timedelta(days=lag_days)
    return datetime.combine(day, time.max, local_timezone).astimezone(timezone.utc)


def digest_reference(day: str, timezone_name: str, now: Optional[datetime] = None) -> datetime:
    """Resolve a content date without changing the actual billing clock."""
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current
    if not day:
        return current
    target = date.fromisoformat(day)
    local_timezone = ZoneInfo(timezone_name)
    local_today = current.astimezone(local_timezone).date()
    if target.isoformat() != day or target > local_today:
        raise ValueError("digest date must be YYYY-MM-DD and cannot be in the future")
    if target == local_today:
        return current
    return datetime.combine(target, time.max, local_timezone).astimezone(timezone.utc)


def digest_end(reference: datetime, timezone_name: str) -> datetime:
    local = reference.astimezone(ZoneInfo(timezone_name))
    return (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc)


def news_snapshot_reference(database, reference: datetime, timezone_name: str, prompt_version: str) -> datetime:
    """Retain the original news lookback when replaying a saved digest."""
    local = reference.astimezone(ZoneInfo(timezone_name))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return database.successful_run_started_at(
        "news", start.astimezone(timezone.utc), digest_end(reference, timezone_name),
        prompt_version=prompt_version, digest_date=local.date().isoformat(),
    ) or reference


def paper_batch_info(database, reference: datetime, settings) -> dict:
    target = paper_reference(reference, settings.timezone, settings.papers.announcement_lag_days)
    day = target.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    digest_date = reference.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    status = database.paper_batch_status(digest_date, day, settings.llm.prompt_version, PAPER_COLLECTION_VERSION)
    messages = {
        "not_scheduled": "目标日期无新公告（周末或官方暂停公告）。",
        "no_new_submissions": "已核对官方公告，目标日期没有新增论文。",
        "no_matches": "该公告批次没有符合关注方向的论文。",
        "available": "该公告批次已完成采集；当前筛选范围没有结果。",
        "unavailable": "该公告批次尚未完成采集，不能判定为没有论文。",
    }
    return {"date": day, "status": status, "message": messages.get(status, messages["unavailable"])}


@dataclass(frozen=True)
class PeriodWindow:
    period: str
    label: str
    published_since: Optional[datetime]
    local_date: str
    published_before: Optional[datetime] = None


def build_period_window(
    kind: str,
    requested: str,
    timezone_name: str,
    lookback_hours: int,
    now: Optional[datetime] = None,
    paper_lag_days: int = 0,
) -> PeriodWindow:
    """Resolve a UI/export period into an explicit UTC publication cutoff.

    Paper views use their own announcement day. News views default to their
    rolling lookback because international feeds publish across time zones.
    """

    if kind not in {"news", "paper"}:
        raise ValueError(f"Unsupported content kind: {kind}")
    if requested not in {"auto", "today", "recent", "all"}:
        raise ValueError(f"Unsupported period: {requested}")

    resolved = ("today" if kind == "paper" else "recent") if requested == "auto" else requested
    local_timezone = ZoneInfo(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if kind == "paper":
        current = paper_reference(current, timezone_name, paper_lag_days)
    current_local = current.astimezone(local_timezone)

    if resolved == "today":
        local_start = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return PeriodWindow(
            period=resolved,
            label=(f"公告 {current_local:%Y-%m-%d}" if kind == "paper" else f"今日（{current_local:%m-%d}）"),
            published_since=local_start.astimezone(timezone.utc),
            local_date=current_local.date().isoformat(),
            published_before=digest_end(current, timezone_name),
        )
    if resolved == "recent":
        hours = max(1, int(lookback_hours))
        return PeriodWindow(
            period=resolved,
            label=f"近 {ceil(hours / 24)} 日",
            published_since=current.astimezone(timezone.utc) - timedelta(hours=hours),
            local_date=current_local.date().isoformat(),
            published_before=digest_end(current, timezone_name) if kind == "paper" else None,
        )
    return PeriodWindow(
        period=resolved,
        label="历史全部",
        published_since=None,
        local_date=current_local.date().isoformat(),
    )
