from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from urllib.parse import urlsplit

from ..config import NetworkSettings, SourceConfig
from ..models import CollectionResult, RadarItem
from ..processing.normalize import (
    canonicalize_url,
    clean_html,
    fingerprint_title,
    parse_datetime,
    unique_preserving_order,
)
from .base import fetch_response


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _matches_allowed_domain(url: str, allowed_domains: Iterable[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(
        hostname == domain.lower().removeprefix("www.")
        or hostname.endswith("." + domain.lower().removeprefix("www."))
        for domain in allowed_domains
    )


def _children(element: ET.Element, names: Iterable[str]) -> List[ET.Element]:
    accepted = {name.lower() for name in names}
    return [child for child in list(element) if _local_name(child.tag) in accepted]


def _first_text(element: ET.Element, names: Iterable[str]) -> str:
    for child in _children(element, names):
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _entry_link(element: ET.Element) -> str:
    candidates = _children(element, ("link",))
    for child in candidates:
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate")
        media_type = child.attrib.get("type", "")
        if href and rel in {"alternate", ""} and media_type != "application/pdf":
            return href
    for child in candidates:
        href = child.attrib.get("href", "").strip()
        text = (child.text or "").strip()
        if href or text:
            return href or text
    return ""


def parse_feed(payload: bytes, source: SourceConfig, kind: str = "news") -> List[RadarItem]:
    root = ET.fromstring(payload)
    entries = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    result: List[RadarItem] = []
    now = datetime.now(timezone.utc)
    for entry in entries:
        title = clean_html(_first_text(entry, ("title",)))
        url = _entry_link(entry)
        if not title or not url:
            continue
        summary = clean_html(
            _first_text(entry, ("content", "encoded", "description", "summary"))
        )[:12000]
        published_raw = _first_text(
            entry, ("published", "pubdate", "updated", "date", "dc:date")
        )
        published_at = parse_datetime(published_raw, now)
        external_id = clean_html(_first_text(entry, ("guid", "id")))
        categories: List[str] = []
        for category in _children(entry, ("category", "subject")):
            value = category.attrib.get("term", "") or "".join(category.itertext())
            if value.strip():
                categories.append(value.strip())
        authors: List[str] = []
        for author in _children(entry, ("author", "creator")):
            value = _first_text(author, ("name",)) or "".join(author.itertext())
            if value.strip():
                authors.append(clean_html(value))
        canonical_url = canonicalize_url(url)
        result.append(
            RadarItem(
                kind=kind,
                title=title,
                url=url,
                canonical_url=canonical_url,
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_type=source.type,
                source_focus=source.focus,
                published_at=published_at,
                summary=summary,
                external_id=external_id,
                authors=unique_preserving_order(authors),
                categories=unique_preserving_order(categories),
                tags=list(source.tags),
                fingerprint=fingerprint_title(title),
                cluster_key=fingerprint_title(title),
                metadata={"feed_url": source.url},
            )
        )
    return result


class RSSCollector:
    def __init__(self, source: SourceConfig, network: NetworkSettings) -> None:
        self.source = source
        self.network = network

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        try:
            response = fetch_response(
                self.source.url,
                user_agent=self.network.user_agent,
                timeout=self.network.timeout_seconds,
                retries=self.network.retries,
                retry_backoff_seconds=self.network.retry_backoff_seconds,
            )
            domain_match = _matches_allowed_domain(
                response.final_url, self.source.allowed_domains
            )
            if not domain_match:
                raise ValueError(
                    f"Feed redirected outside allowed domains: {response.final_url}"
                )
            items = parse_feed(response.payload, self.source)
            return CollectionResult(
                source_id=self.source.id,
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name=self.source.name,
                source_url=self.source.url,
                final_url=response.final_url,
                http_status=response.status,
                domain_match=True,
            )
        except Exception as exc:
            return CollectionResult(
                source_id=self.source.id,
                error=f"{exc.__class__.__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name=self.source.name,
                source_url=self.source.url,
                domain_match=False,
            )
