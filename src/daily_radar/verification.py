from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from .collectors.base import build_http_opener
from .config import NetworkSettings, SourceConfig
from .models import RadarItem


@dataclass(frozen=True)
class VerificationResult:
    status: str
    checked_at: str
    original_url: str
    final_url: str
    http_status: int
    domain: str
    domain_match: bool
    source_tier: int
    method: str
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.status in {
            "verified-primary",
            "verified-publisher",
            "verified-community",
            "verified-link",
            "access-restricted",
        }


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def domain_matches(url: str, allowed_domains: Iterable[str]) -> bool:
    hostname = _hostname(url)
    for domain in allowed_domains:
        allowed = domain.lower().strip().removeprefix("www.")
        if hostname == allowed or hostname.endswith("." + allowed):
            return True
    return False


def _status_for(source: SourceConfig, match: bool, reachable: bool) -> str:
    if not reachable:
        return "unverified"
    if match and source.tier == 1:
        return "verified-primary"
    if match and source.type == "community":
        return "verified-community"
    if match:
        return "verified-publisher"
    if source.allow_external_links:
        return "verified-link"
    return "domain-mismatch"


def verify_url(
    url: str,
    source: SourceConfig,
    network: NetworkSettings,
) -> VerificationResult:
    checked_at = datetime.now(timezone.utc).isoformat()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return VerificationResult(
            status="invalid-url",
            checked_at=checked_at,
            original_url=url,
            final_url="",
            http_status=0,
            domain="",
            domain_match=False,
            source_tier=source.tier,
            method="url-structure",
            reason="Only absolute HTTP(S) source URLs are accepted",
        )

    opener = build_http_opener()
    headers = {
        "User-Agent": network.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    }
    last_error = ""
    for method in ("HEAD", "GET"):
        request_headers = dict(headers)
        if method == "GET":
            request_headers["Range"] = "bytes=0-1023"
        request = Request(url, method=method, headers=request_headers)
        try:
            with opener.open(request, timeout=network.timeout_seconds) as response:
                final_url = response.geturl()
                status_code = int(getattr(response, "status", 200) or 200)
            match = domain_matches(final_url, source.allowed_domains)
            status = _status_for(source, match, 200 <= status_code < 400)
            return VerificationResult(
                status=status,
                checked_at=checked_at,
                original_url=url,
                final_url=final_url,
                http_status=status_code,
                domain=_hostname(final_url),
                domain_match=match,
                source_tier=source.tier,
                method=f"http-{method.lower()}",
                reason=(
                    "External link discovered through a community feed"
                    if status == "verified-link"
                    else ""
                ),
            )
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code in {401, 403, 429}:
                final_url = exc.geturl() or url
                match = domain_matches(final_url, source.allowed_domains)
                if not match and not source.allow_external_links:
                    status = "domain-mismatch"
                else:
                    status = "access-restricted"
                return VerificationResult(
                    status=status,
                    checked_at=checked_at,
                    original_url=url,
                    final_url=final_url,
                    http_status=exc.code,
                    domain=_hostname(final_url),
                    domain_match=match,
                    source_tier=source.tier,
                    method=f"http-{method.lower()}",
                    reason="Publisher blocks automated verification but the endpoint exists",
                )
            if exc.code in {404, 410}:
                break
            if method == "HEAD" and exc.code in {400, 405, 501}:
                continue
            break
        except (URLError, TimeoutError, OSError) as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            if method == "HEAD":
                continue
            break

    match = domain_matches(url, source.allowed_domains)
    return VerificationResult(
        status="unverified" if last_error else "failed",
        checked_at=checked_at,
        original_url=url,
        final_url=url,
        http_status=0,
        domain=_hostname(url),
        domain_match=match,
        source_tier=source.tier,
        method="http-head-get",
        reason=last_error or "Source could not be verified",
    )


def arxiv_api_verification(item: RadarItem, api_url: str) -> VerificationResult:
    expected = item.external_id
    path_id = item.canonical_url.rstrip("/").rsplit("/", 1)[-1]
    valid = bool(expected and path_id == expected and _hostname(item.canonical_url) == "arxiv.org")
    return VerificationResult(
        status="verified-primary" if valid else "invalid-record",
        checked_at=datetime.now(timezone.utc).isoformat(),
        original_url=item.url,
        final_url=item.canonical_url,
        http_status=200,
        domain="arxiv.org",
        domain_match=valid,
        source_tier=1,
        method="arxiv-api-entry",
        reason="arXiv API ID matches the canonical abstract URL" if valid else "arXiv ID mismatch",
    )


def verify_news_items(
    items: List[RadarItem],
    sources: Mapping[str, SourceConfig],
    network: NetworkSettings,
) -> List[RadarItem]:
    jobs: Dict[Tuple[str, str], SourceConfig] = {}
    for item in items:
        source = sources.get(item.source_id)
        if source:
            jobs[(item.url, item.source_id)] = source
        for alternate in item.metadata.get("alternate_sources", []):
            alternate_source = sources.get(alternate.get("source_id", ""))
            if alternate_source and alternate.get("url"):
                jobs[(alternate["url"], alternate_source.id)] = alternate_source

    verified: Dict[Tuple[str, str], VerificationResult] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as executor:
        futures = {
            executor.submit(verify_url, url, source, network): (url, source_id)
            for (url, source_id), source in jobs.items()
        }
        for future in as_completed(futures):
            verified[futures[future]] = future.result()

    usable_items: List[RadarItem] = []
    for item in items:
        result = verified.get((item.url, item.source_id))
        if result is None:
            continue
        item.metadata["provenance"] = asdict(result)
        valid_alternates = []
        for alternate in item.metadata.get("alternate_sources", []):
            key = (alternate.get("url", ""), alternate.get("source_id", ""))
            alternate_result = verified.get(key)
            if alternate_result and alternate_result.usable:
                enriched = dict(alternate)
                enriched["provenance"] = asdict(alternate_result)
                valid_alternates.append(enriched)
        item.metadata["alternate_sources"] = valid_alternates
        item.metadata["source_count"] = 1 + len(valid_alternates)
        if result.usable:
            usable_items.append(item)
    return usable_items


def audit_database(database, settings, kind: str = "all", limit: int = 500) -> Dict[str, int]:
    from .config import load_sources

    source_map = {source.id: source for source in load_sources(settings.sources_path)}
    paper_source = SourceConfig(
        id="arxiv",
        name="arXiv",
        url="https://arxiv.org/",
        tier=1,
        type="paper-api",
        focus=1.0,
        allowed_domains=["arxiv.org"],
    )
    selected_kind: Optional[str] = None if kind == "all" else kind
    items = database.list_items(kind=selected_kind, limit=limit)
    jobs = {}
    for item in items:
        if item.get("metadata", {}).get("demo"):
            continue
        source = paper_source if item["kind"] == "paper" else source_map.get(item["source_id"])
        if source:
            jobs[item["id"]] = (item, source)

    results: Dict[int, VerificationResult] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as executor:
        futures = {
            executor.submit(verify_url, item["url"], source, settings.network): item_id
            for item_id, (item, source) in jobs.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    counts = {
        "checked": 0,
        "verified": 0,
        "restricted": 0,
        "unverified": 0,
        "unknown_source": max(0, len(items) - len(jobs)),
    }
    for item_id, result in results.items():
        item = jobs[item_id][0]
        metadata = dict(item.get("metadata", {}))
        payload = asdict(result)
        if item["kind"] == "paper":
            external_id = item.get("external_id", "")
            path_id = item["canonical_url"].rstrip("/").rsplit("/", 1)[-1]
            if not external_id or external_id != path_id:
                payload.update(
                    status="invalid-record",
                    domain_match=False,
                    reason="Stored arXiv ID does not match its canonical URL",
                )
            elif payload["status"].startswith("verified-"):
                payload.update(
                    method="arxiv-id+http-head",
                    reason="Stored arXiv ID matches a reachable official abstract URL",
                )
        metadata["provenance"] = payload
        database.update_item_metadata(item_id, metadata)
        counts["checked"] += 1
        status = payload["status"]
        if status.startswith("verified-"):
            counts["verified"] += 1
        elif status == "access-restricted":
            counts["restricted"] += 1
        else:
            counts["unverified"] += 1
    return counts
