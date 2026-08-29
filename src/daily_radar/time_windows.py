from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class PeriodWindow:
    period: str
    label: str
    published_since: Optional[datetime]
    local_date: str


def build_period_window(
    kind: str,
    requested: str,
    timezone_name: str,
    lookback_hours: int,
    now: Optional[datetime] = None,
) -> PeriodWindow:
    """Resolve a UI/export period into an explicit UTC publication cutoff.

    Paper views default to the local calendar day. News views default to their
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
    current_local = current.astimezone(local_timezone)

    if resolved == "today":
        local_start = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return PeriodWindow(
            period=resolved,
            label=f"今日（{current_local:%m-%d}）",
            published_since=local_start.astimezone(timezone.utc),
            local_date=current_local.date().isoformat(),
        )
    if resolved == "recent":
        hours = max(1, int(lookback_hours))
        return PeriodWindow(
            period=resolved,
            label=f"近 {ceil(hours / 24)} 日",
            published_since=current.astimezone(timezone.utc) - timedelta(hours=hours),
            local_date=current_local.date().isoformat(),
        )
    return PeriodWindow(
        period=resolved,
        label="历史全部",
        published_since=None,
        local_date=current_local.date().isoformat(),
    )
