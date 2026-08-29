from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class RadarItem:
    kind: str
    title: str
    url: str
    canonical_url: str
    source_id: str
    source_name: str
    source_tier: int
    published_at: datetime
    summary: str = ""
    external_id: str = ""
    source_type: str = "rss"
    source_focus: float = 0.5
    authors: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    category: str = "other"
    tags: List[str] = field(default_factory=list)
    score: float = 0.0
    component_scores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    cluster_key: str = ""
    is_important: bool = False
    fetched_at: datetime = field(default_factory=utc_now)
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind not in {"news", "paper"}:
            raise ValueError("kind must be 'news' or 'paper'")
        self.published_at = ensure_utc(self.published_at)
        self.fetched_at = ensure_utc(self.fetched_at)


@dataclass
class CollectionResult:
    source_id: str
    items: List[RadarItem] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0
    source_name: str = ""
    source_url: str = ""
    final_url: str = ""
    http_status: int = 0
    domain_match: bool = False


@dataclass
class RunSummary:
    kind: str
    started_at: datetime
    finished_at: datetime
    fetched: int
    accepted: int
    important: int
    sources_ok: int
    sources_failed: int
    errors: List[str] = field(default_factory=list)
    run_id: Optional[int] = None
