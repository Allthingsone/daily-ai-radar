from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit

from ..config import NetworkSettings, SourceConfig
from ..models import CollectionResult, RadarItem
from ..processing.normalize import (
    canonicalize_url,
    clean_html,
    fingerprint_title,
    parse_datetime_with_status,
)
from .base import fetch_response
from .rss import _matches_allowed_domain


def parse_wordpress_post(post: Dict[str, Any], source: SourceConfig) -> Optional[RadarItem]:
    """Read public publisher content; account popularity is not article heat."""
    if not isinstance(post, dict) or post.get("status") != "publish":
        return None
    url = post.get("link", "")
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        return None
    if not _matches_allowed_domain(url, source.allowed_domains):
        return None
    title = post.get("title", {})
    content = post.get("content", {})
    if not isinstance(title, dict) or not isinstance(content, dict):
        return None
    if content.get("protected"):
        return None
    title = title.get("rendered", "")
    body = content.get("rendered", "")
    if not isinstance(title, str) or not isinstance(body, str):
        return None
    title = clean_html(title)
    body = clean_html(body)
    # WordPress date_gmt is explicitly UTC. Never substitute modified_gmt or
    # the fetch time when the first-publication timestamp is missing.
    published_raw = post.get("date_gmt", "")
    if not isinstance(published_raw, str):
        return None
    published_at, verified = parse_datetime_with_status(published_raw)
    if not title or not body or not verified:
        return None
    return RadarItem(
        kind="news", title=title, summary=body[:12000],
        url=url, canonical_url=canonicalize_url(url),
        source_id=source.id, source_name=source.name, source_tier=source.tier,
        source_type=source.type, source_focus=source.focus,
        published_at=published_at, external_id=str(post.get("id", "")),
        tags=list(source.tags), fingerprint=fingerprint_title(title),
        cluster_key=fingerprint_title(title),
        metadata={
            "feed_url": source.url,
            "collection_method": "wordpress-public-posts",
            "published_raw": published_raw,
            "published_at_verified": True,
        },
    )


class WordPressCollector:
    """Fetch the public article API used by the publisher's own website."""

    def __init__(self, source: SourceConfig, network: NetworkSettings) -> None:
        self.source = source
        self.network = network

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(
            source_id=self.source.id, source_name=self.source.name,
            source_url=self.source.url,
        )
        try:
            if (urlsplit(self.source.url).scheme != "https"
                    or not _matches_allowed_domain(self.source.url, self.source.allowed_domains)):
                raise ValueError("Publisher API must use an allowed HTTPS domain")
            limit = min(100, max(1, self.source.max_items))
            query = urlencode({
                "per_page": limit, "orderby": "date", "order": "desc",
                "status": "publish",
                "_fields": "id,link,date_gmt,status,title,content",
            })
            response = fetch_response(
                self.source.url + "?" + query,
                user_agent=self.network.user_agent,
                timeout=self.network.timeout_seconds,
                retries=self.network.retries,
                retry_backoff_seconds=self.network.retry_backoff_seconds,
            )
            result.final_url = response.final_url
            result.http_status = response.status
            if not _matches_allowed_domain(response.final_url, self.source.allowed_domains):
                raise ValueError("Publisher API redirected outside allowed domains")
            result.domain_match = True
            posts = json.loads(response.payload)
            if not isinstance(posts, list):
                raise ValueError("Publisher API did not return a public post array")
            items: List[RadarItem] = []
            for post in posts[:limit]:
                item = parse_wordpress_post(post, self.source)
                if item is not None:
                    items.append(item)
            result.details = {"returned": len(posts[:limit]), "discarded": len(posts[:limit]) - len(items)}
            if posts and not items:
                raise ValueError("Publisher returned posts but no verifiable public articles")
            result.items = items
        except Exception as exc:
            result.error = f"{exc.__class__.__name__}: {exc}"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
