from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import NetworkSettings, SourceConfig
from ..models import CollectionResult, RadarItem
from ..processing.normalize import canonicalize_url, clean_html, fingerprint_title
from .community import _heat_qualified


SEARCH_ENDPOINT = "https://developer.zhihu.com/api/v1/content/zhihu_search"


class _NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # This request carries a secret. Never forward it to a redirect target.
        return None


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return 0


def parse_zhihu_result(entry: Dict[str, Any], source: SourceConfig) -> Optional[RadarItem]:
    if not isinstance(entry, dict):
        return None
    url = entry.get("Url", "")
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    identifier = str(entry.get("ContentID", ""))
    content_type = str(entry.get("ContentType", "")).lower()
    paths = {
        "question": ("www.zhihu.com", r"/question/(\d+)"),
        "answer": ("www.zhihu.com", r"/question/\d+/answer/(\d+)"),
        "article": ("zhuanlan.zhihu.com", r"/p/(\d+)"),
    }
    expected_host, pattern = paths.get(content_type, ("", "(?!)"))
    match = re.fullmatch(pattern, parts.path.rstrip("/"))
    if (parts.scheme != "https" or parts.netloc != expected_host
            or not match or match.group(1) != identifier):
        return None
    # EditTime may mean either publication OR modification per the API docs.
    # Only CreateTime proves that a discussion belongs to the news window.
    created = _count(entry.get("CreateTime"))
    if not created:
        return None
    try:
        published_at = datetime.fromtimestamp(created, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    title = entry.get("Title", "")
    summary = entry.get("ContentText", "")
    if not isinstance(title, str) or not isinstance(summary, str):
        return None
    title = clean_html(title)
    if not title:
        return None
    points = _count(entry.get("VoteUpCount"))
    comments = _count(entry.get("CommentCount"))
    qualified = _heat_qualified(source, rank=0, points=points, comments=comments)
    author = clean_html(str(entry.get("AuthorName", "")))
    return RadarItem(
        kind="news", title=title, url=url, canonical_url=canonicalize_url(url),
        summary=clean_html(summary)[:12000],
        source_id=source.id, source_name=source.name, source_tier=source.tier,
        source_type=source.type, source_focus=source.focus,
        published_at=published_at, external_id=identifier,
        authors=[author] if author else [], tags=list(source.tags),
        fingerprint=fingerprint_title(title), cluster_key=fingerprint_title(title),
        metadata={
            "feed_url": source.url, "collection_method": "zhihu-official-search",
            "published_raw": str(created), "published_at_verified": True,
            "discussion_url": url,
            "community_signals": [{
                "platform": source.community_platform or "知乎",
                "signal_type": "votes-comments", "rank": 0,
                "points": points, "likes": points, "comments": comments,
                "qualified": qualified, "discussion_url": url,
            }],
        },
    )


class ZhihuSearchCollector:
    """Use dated, attributable official search results instead of undated hot lists."""

    def __init__(self, source: SourceConfig, network: NetworkSettings,
                 lookback_hours: int = 48) -> None:
        self.source = source
        self.network = network
        self.lookback_hours = lookback_hours

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        result = CollectionResult(
            source_id=self.source.id, source_name=self.source.name,
            source_url=self.source.url,
            # Bound authenticated requests: no pipeline replay of all queries
            # after a partial failure, missing secret, quota error or rate limit.
            details={"retryable": False, "queries_completed": 0, "discarded": 0},
        )
        secret = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
        if not secret:
            result.error = "ZHIHU_ACCESS_SECRET is not configured; Zhihu source skipped"
            result.details["status"] = "not-configured"
            return result
        try:
            if self.source.url != SEARCH_ENDPOINT:
                raise ValueError("Zhihu credentials require the official search endpoint")
            queries = list(dict.fromkeys(q.strip() for q in self.source.queries if q.strip()))
            if not queries:
                raise ValueError("Zhihu source requires search queries")
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=self.lookback_hours)
            future_limit = now + timedelta(hours=2)
            seen = set()
            returned = 0
            dated = 0
            request_limit = min(50, max(1, self.source.max_items))
            requested = 0
            opener = build_opener(_NoCredentialRedirect())
            for query in queries[:5]:
                count = min(10, request_limit - requested)
                if count <= 0:
                    break
                parameters = urlencode({
                    "Query": query, "Count": count,
                    "SortBy": f"CreateTime:desc:({int(cutoff.timestamp())},{int(future_limit.timestamp())})",
                })
                request = Request(SEARCH_ENDPOINT + "?" + parameters, headers={
                    "Authorization": "Bearer " + secret,
                    "X-Request-Timestamp": str(int(time.time())),
                    "Content-Type": "application/json", "Accept": "application/json",
                    "User-Agent": self.network.user_agent,
                })
                requested += count
                with opener.open(request, timeout=self.network.timeout_seconds) as response:
                    result.final_url = response.geturl()
                    result.http_status = response.status
                    payload = json.load(response)
                result.domain_match = True
                if not isinstance(payload, dict) or payload.get("Code") != 0:
                    code = payload.get("Code") if isinstance(payload, dict) else "invalid-json"
                    # Do not echo the response Message: it may contain request credentials.
                    raise ValueError(f"Zhihu API returned error code {code}")
                data = payload.get("Data")
                entries = data.get("Items") if isinstance(data, dict) else None
                if not isinstance(entries, list):
                    raise ValueError("Zhihu API did not return an Items array")
                result.details["queries_completed"] += 1
                for entry in entries[:count]:
                    returned += 1
                    item = parse_zhihu_result(entry, self.source)
                    if item is None:
                        result.details["discarded"] += 1
                        continue
                    dated += 1
                    if not cutoff <= item.published_at <= future_limit:
                        continue
                    if item.canonical_url not in seen:
                        seen.add(item.canonical_url)
                        result.items.append(item)
            if returned and not dated:
                raise ValueError("Zhihu returned results but no verifiable creation dates and identities")
        except HTTPError as exc:
            result.http_status = exc.code
            result.error = f"Zhihu API returned HTTP {exc.code}"
        except Exception as exc:
            result.error = f"{exc.__class__.__name__}: {exc}".replace(secret, "[redacted]")
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
