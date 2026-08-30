from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from ..config import NetworkSettings, SourceConfig
from ..models import CollectionResult, RadarItem
from ..processing.normalize import (
    canonicalize_url,
    clean_html,
    fingerprint_title,
    parse_datetime_with_status,
)
from .base import FetchResponse, fetch_response


def _domain_matches(url: str, allowed_domains: Iterable[str]) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return any(
        hostname == domain.lower().removeprefix("www.")
        or hostname.endswith("." + domain.lower().removeprefix("www."))
        for domain in allowed_domains
    )


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _heat_qualified(
    source: SourceConfig, *, rank: int, points: int, comments: int
) -> bool:
    if source.community_rank_limit > 0 and rank > source.community_rank_limit:
        return False
    thresholds = []
    if source.community_min_points > 0:
        thresholds.append(points >= source.community_min_points)
    if source.community_min_comments > 0:
        thresholds.append(comments >= source.community_min_comments)
    return any(thresholds) if thresholds else source.community_rank_limit > 0


def _fetch(
    url: str, network: NetworkSettings, *, retries: Optional[int] = None
) -> FetchResponse:
    return fetch_response(
        url,
        user_agent=network.user_agent,
        timeout=network.timeout_seconds,
        retries=network.retries if retries is None else retries,
        retry_backoff_seconds=network.retry_backoff_seconds,
    )


def parse_hackernews_story(
    payload: Dict[str, Any], source: SourceConfig, rank: int
) -> Optional[RadarItem]:
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "story"
        or payload.get("deleted")
        or payload.get("dead")
    ):
        return None
    story_id = _integer(payload.get("id"))
    published_raw = _integer(payload.get("time"))
    title = clean_html(str(payload.get("title", "")))
    if not story_id or not published_raw or not title:
        return None
    discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
    target_url = str(payload.get("url", "")).strip() or discussion_url
    if urlsplit(target_url).scheme not in {"http", "https"}:
        target_url = discussion_url
    points = _integer(payload.get("score"))
    comments = _integer(payload.get("descendants"))
    qualified = _heat_qualified(
        source, rank=rank, points=points, comments=comments
    )
    published_at = datetime.fromtimestamp(published_raw, tz=timezone.utc)
    text = clean_html(str(payload.get("text", "")))[:12000]
    platform = source.community_platform or "Hacker News"
    return RadarItem(
        kind="news",
        title=title,
        url=target_url,
        canonical_url=canonicalize_url(target_url),
        source_id=source.id,
        source_name=source.name,
        source_tier=source.tier,
        source_type=source.type,
        source_focus=source.focus,
        published_at=published_at,
        summary=text,
        external_id=str(story_id),
        authors=[clean_html(str(payload.get("by", "")))] if payload.get("by") else [],
        tags=list(source.tags),
        fingerprint=fingerprint_title(title),
        cluster_key=fingerprint_title(title),
        metadata={
            "feed_url": source.url,
            "published_raw": str(published_raw),
            "published_at_verified": True,
            "discussion_url": discussion_url,
            "community_signals": [
                {
                    "platform": platform,
                    "signal_type": "points-comments-rank",
                    "rank": rank,
                    "points": points,
                    "comments": comments,
                    "qualified": qualified,
                    "discussion_url": discussion_url,
                }
            ],
        },
    )


class HackerNewsCollector:
    """Collect ranked stories and interaction counts from the official HN API."""

    def __init__(self, source: SourceConfig, network: NetworkSettings) -> None:
        self.source = source
        self.network = network

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        try:
            index_response = _fetch(self.source.url, self.network)
            if not _domain_matches(index_response.final_url, self.source.allowed_domains):
                raise ValueError(
                    "Hacker News API redirected outside allowed domains: "
                    + index_response.final_url
                )
            story_ids = json.loads(index_response.payload.decode("utf-8"))
            if not isinstance(story_ids, list):
                raise ValueError("Hacker News API did not return a story ID list")
            ranked_ids = [
                _integer(value) for value in story_ids[: max(1, self.source.max_items)]
            ]
            ranked_ids = [value for value in ranked_ids if value]
            stories: Dict[int, Dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(ranked_ids)))) as pool:
                futures = {
                    pool.submit(
                        _fetch,
                        f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                        self.network,
                        retries=1,
                    ): story_id
                    for story_id in ranked_ids
                }
                for future in as_completed(futures):
                    story_id = futures[future]
                    try:
                        response = future.result()
                        decoded = json.loads(response.payload.decode("utf-8"))
                        if isinstance(decoded, dict):
                            stories[story_id] = decoded
                    except Exception:
                        # One deleted or transiently unavailable story must not
                        # turn the entire ranked source into a failed run.
                        continue
            items = []
            for rank, story_id in enumerate(ranked_ids, start=1):
                item = parse_hackernews_story(stories.get(story_id, {}), self.source, rank)
                if item is not None:
                    items.append(item)
            return CollectionResult(
                source_id=self.source.id,
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name=self.source.name,
                source_url=self.source.url,
                final_url=index_response.final_url,
                http_status=index_response.status,
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


class _ArticleMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: Dict[str, str] = {}
        self.json_ld_scripts: List[str] = []
        self._json_ld_chunks: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_name = tag.lower()
        values = {str(key).lower(): value or "" for key, value in attrs}
        if tag_name == "script" and values.get("type", "").lower() == "application/ld+json":
            self._json_ld_chunks = []
            return
        if tag_name != "meta":
            return
        key = (values.get("property") or values.get("name") or "").lower()
        content = values.get("content", "").strip()
        if key and content and key not in self.values:
            self.values[key] = content

    def handle_data(self, data: str) -> None:
        if self._json_ld_chunks is not None:
            self._json_ld_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_ld_chunks is not None:
            value = "".join(self._json_ld_chunks).strip()
            if value:
                self.json_ld_scripts.append(value)
            self._json_ld_chunks = None


def parse_csdn_article_metadata(payload: bytes) -> Dict[str, str]:
    parser = _ArticleMetaParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    return {
        "published_at": parser.values.get("article:published_time", ""),
        "title": parser.values.get("og:title", ""),
        "description": parser.values.get("og:description", "")
        or parser.values.get("description", ""),
    }


def parse_juejin_article_metadata(payload: bytes) -> Dict[str, str]:
    """Read the article's public Schema.org metadata, including its real date."""

    parser = _ArticleMetaParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    for raw in parser.json_ld_scripts:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        entries = decoded if isinstance(decoded, list) else [decoded]
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("@type") != "BlogPosting":
                continue
            author = entry.get("author", {})
            author_name = (
                str(author.get("name", "")) if isinstance(author, dict) else ""
            )
            return {
                "published_at": str(entry.get("datePublished", "")),
                "title": str(entry.get("headline", "")),
                "description": str(entry.get("description", "")),
                "author": author_name,
            }
    return {
        "published_at": "",
        "title": parser.values.get("og:title", ""),
        "description": parser.values.get("description", ""),
        "author": "",
    }


def parse_juejin_ranked_article(
    entry: Dict[str, Any],
    article_payload: bytes,
    final_url: str,
    source: SourceConfig,
    rank: int,
) -> Optional[RadarItem]:
    content = entry.get("content", {})
    counter = entry.get("content_counter", {})
    author = entry.get("author", {})
    if not isinstance(content, dict) or not isinstance(counter, dict):
        return None
    content_id = str(content.get("content_id", "")).strip()
    expected_url = f"https://juejin.cn/post/{content_id}" if content_id else ""
    final_path = urlsplit(final_url).path.rstrip("/")
    if (
        not content_id
        or not expected_url
        or not _domain_matches(final_url, source.allowed_domains)
        or final_path != f"/post/{content_id}"
    ):
        return None
    article_meta = parse_juejin_article_metadata(article_payload)
    published_raw = article_meta["published_at"]
    published_at, published_verified = parse_datetime_with_status(
        published_raw, datetime.now(timezone.utc)
    )
    title = clean_html(str(content.get("title", "")) or article_meta["title"])
    if not title or not published_verified:
        return None
    points = _integer(counter.get("hot_rank"))
    comments = _integer(counter.get("comment_count"))
    qualified = _heat_qualified(
        source, rank=rank, points=points, comments=comments
    )
    author_name = ""
    if isinstance(author, dict):
        author_name = clean_html(str(author.get("name", "")))
    author_name = author_name or clean_html(article_meta["author"])
    platform = source.community_platform or "掘金"
    return RadarItem(
        kind="news",
        title=title,
        url=final_url,
        canonical_url=canonicalize_url(final_url),
        source_id=source.id,
        source_name=source.name,
        source_tier=source.tier,
        source_type=source.type,
        source_focus=source.focus,
        published_at=published_at,
        summary=clean_html(article_meta["description"])[:12000],
        external_id=content_id,
        authors=[author_name] if author_name else [],
        tags=list(source.tags),
        fingerprint=fingerprint_title(title),
        cluster_key=fingerprint_title(title),
        metadata={
            "feed_url": source.url,
            "published_raw": published_raw,
            "published_at_verified": True,
            "discussion_url": expected_url,
            "community_signals": [
                {
                    "platform": platform,
                    "signal_type": "ai-hot-rank-engagement",
                    "rank": rank,
                    "points": points,
                    "comments": comments,
                    "views": _integer(counter.get("view")),
                    "favorites": _integer(counter.get("collect")),
                    "likes": _integer(counter.get("like")),
                    "qualified": qualified,
                    "discussion_url": expected_url,
                }
            ],
        },
    )


class JuejinHotCollector:
    """Collect Juejin's AI hot list with engagement and article dates."""

    def __init__(self, source: SourceConfig, network: NetworkSettings) -> None:
        self.source = source
        self.network = network

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        try:
            list_response = _fetch(self.source.url, self.network)
            if not _domain_matches(list_response.final_url, self.source.allowed_domains):
                raise ValueError(
                    "Juejin hot list redirected outside allowed domains: "
                    + list_response.final_url
                )
            payload = json.loads(list_response.payload.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("err_no") != 0:
                raise ValueError("Juejin hot list returned an error response")
            entries = payload.get("data")
            if not isinstance(entries, list):
                raise ValueError("Juejin hot list did not return a data array")
            ranked_entries = []
            for entry in entries[: max(1, self.source.max_items)]:
                content = entry.get("content", {}) if isinstance(entry, dict) else {}
                if isinstance(content, dict) and content.get("content_id"):
                    ranked_entries.append(entry)
            responses: Dict[str, FetchResponse] = {}
            with ThreadPoolExecutor(
                max_workers=min(6, max(1, len(ranked_entries)))
            ) as pool:
                futures = {}
                for entry in ranked_entries:
                    content_id = str(entry["content"]["content_id"])
                    url = f"https://juejin.cn/post/{content_id}"
                    futures[
                        pool.submit(_fetch, url, self.network, retries=1)
                    ] = url
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        responses[url] = future.result()
                    except Exception:
                        continue
            items = []
            for rank, entry in enumerate(ranked_entries, start=1):
                content_id = str(entry["content"]["content_id"])
                expected_url = f"https://juejin.cn/post/{content_id}"
                response = responses.get(expected_url)
                if response is None:
                    continue
                item = parse_juejin_ranked_article(
                    entry,
                    response.payload,
                    response.final_url,
                    self.source,
                    rank,
                )
                if item is not None:
                    items.append(item)
            if ranked_entries and not items:
                raise RuntimeError(
                    "Juejin returned ranked entries but no verifiable publication dates"
                )
            return CollectionResult(
                source_id=self.source.id,
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name=self.source.name,
                source_url=self.source.url,
                final_url=list_response.final_url,
                http_status=list_response.status,
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


def _build_csdn_item(
    entry: Dict[str, Any],
    article_response: FetchResponse,
    source: SourceConfig,
    rank: int,
) -> Optional[RadarItem]:
    url = str(entry.get("articleDetailUrl", "")).strip()
    if not url or not _domain_matches(article_response.final_url, source.allowed_domains):
        return None
    article_meta = parse_csdn_article_metadata(article_response.payload)
    published_raw = article_meta["published_at"]
    published_at, published_verified = parse_datetime_with_status(
        published_raw, datetime.now(timezone.utc)
    )
    title = clean_html(str(entry.get("articleTitle", "")))
    if not title or not published_verified:
        return None
    points = _integer(entry.get("hotRankScore"))
    comments = _integer(entry.get("commentCount"))
    qualified = _heat_qualified(
        source, rank=rank, points=points, comments=comments
    )
    platform = source.community_platform or "CSDN"
    return RadarItem(
        kind="news",
        title=title,
        url=article_response.final_url,
        canonical_url=canonicalize_url(article_response.final_url),
        source_id=source.id,
        source_name=source.name,
        source_tier=source.tier,
        source_type=source.type,
        source_focus=source.focus,
        published_at=published_at,
        summary=clean_html(article_meta["description"])[:12000],
        external_id=str(entry.get("productId", "")),
        authors=[clean_html(str(entry.get("nickName", "")))] if entry.get("nickName") else [],
        tags=list(source.tags),
        fingerprint=fingerprint_title(title),
        cluster_key=fingerprint_title(title),
        metadata={
            "feed_url": source.url,
            "published_raw": published_raw,
            "published_at_verified": True,
            "discussion_url": article_response.final_url,
            "community_signals": [
                {
                    "platform": platform,
                    "signal_type": "hot-rank-engagement",
                    "rank": rank,
                    "points": points,
                    "comments": comments,
                    "views": _integer(entry.get("viewCount")),
                    "favorites": _integer(entry.get("favorCount")),
                    "qualified": qualified,
                    "discussion_url": article_response.final_url,
                    "period": clean_html(str(entry.get("period", ""))),
                }
            ],
        },
    )


class CSDNHotCollector:
    """Collect CSDN's public hot list and verify dates on each original post."""

    def __init__(self, source: SourceConfig, network: NetworkSettings) -> None:
        self.source = source
        self.network = network

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        try:
            list_response = _fetch(self.source.url, self.network)
            if not _domain_matches(list_response.final_url, self.source.allowed_domains):
                raise ValueError(
                    "CSDN hot list redirected outside allowed domains: "
                    + list_response.final_url
                )
            payload = json.loads(list_response.payload.decode("utf-8"))
            entries = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise ValueError("CSDN hot list did not return a data array")
            ranked_entries = [
                entry
                for entry in entries[: max(1, self.source.max_items)]
                if isinstance(entry, dict) and entry.get("articleDetailUrl")
            ]
            responses: Dict[str, FetchResponse] = {}
            with ThreadPoolExecutor(
                max_workers=min(6, max(1, len(ranked_entries)))
            ) as pool:
                futures = {
                    pool.submit(
                        _fetch,
                        str(entry["articleDetailUrl"]),
                        self.network,
                        retries=1,
                    ): str(entry["articleDetailUrl"])
                    for entry in ranked_entries
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        responses[url] = future.result()
                    except Exception:
                        continue
            items = []
            for rank, entry in enumerate(ranked_entries, start=1):
                url = str(entry["articleDetailUrl"])
                response = responses.get(url)
                if response is None:
                    continue
                item = _build_csdn_item(entry, response, self.source, rank)
                if item is not None:
                    items.append(item)
            if ranked_entries and not items:
                raise RuntimeError(
                    "CSDN returned ranked entries but article publication metadata "
                    "was unavailable"
                )
            return CollectionResult(
                source_id=self.source.id,
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name=self.source.name,
                source_url=self.source.url,
                final_url=list_response.final_url,
                http_status=list_response.status,
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
