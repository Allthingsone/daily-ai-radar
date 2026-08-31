from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..config import NetworkSettings, PaperSettings
from ..models import CollectionResult, RadarItem
from ..processing.normalize import (
    canonicalize_url,
    clean_html,
    fingerprint_title,
    parse_datetime_with_status,
    unique_preserving_order,
)
from .base import fetch_response


ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_EASTERN_TIMEZONE = "America/New_York"
ARXIV_ANNOUNCEMENT_WEEKDAYS = {0, 1, 2, 3, 6}  # Monday-Thursday and Sunday


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(entry: ET.Element, name: str) -> str:
    for child in list(entry):
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


class ArxivCollector:
    def __init__(
        self,
        papers: PaperSettings,
        network: NetworkSettings,
        timezone_name: str = "Asia/Shanghai",
        clock: Optional[Callable[[], datetime]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.papers = papers
        self.network = network
        self.timezone_name = timezone_name
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleeper or time.sleep

    def daily_window(
        self, now: Optional[datetime] = None
    ) -> Tuple[datetime, datetime]:
        current = now or self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current_local = current.astimezone(ZoneInfo(self.timezone_name))
        local_start = current_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        local_end = local_start + timedelta(days=1)
        return (
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )

    def announcement_window(
        self, now: Optional[datetime] = None
    ) -> Tuple[datetime, datetime, datetime]:
        """Return the submission interval for the latest scheduled mailing.

        arXiv announces at 20:00 US Eastern on Sunday through Thursday.  The
        official schedule maps each mailing to a submission interval ending at
        14:00 Eastern.  The returned tuple is
        ``(submitted_since, submitted_before, announced_at)`` in UTC.
        """

        current = now or self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        eastern = current.astimezone(ZoneInfo(ARXIV_EASTERN_TIMEZONE))
        announcement = eastern.replace(
            hour=20, minute=0, second=0, microsecond=0
        )
        if eastern < announcement:
            announcement -= timedelta(days=1)
        while announcement.weekday() not in ARXIV_ANNOUNCEMENT_WEEKDAYS:
            announcement -= timedelta(days=1)

        weekday = announcement.weekday()
        if weekday == 6:  # Sunday: Thursday 14:00 through Friday 14:00.
            start_days, end_days = 3, 2
        elif weekday == 0:  # Monday: Friday 14:00 through Monday 14:00.
            start_days, end_days = 3, 0
        else:  # Tuesday-Thursday: previous weekday 14:00 through today 14:00.
            start_days, end_days = 1, 0
        submitted_since = (announcement - timedelta(days=start_days)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        submitted_before = (announcement - timedelta(days=end_days)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        return (
            submitted_since.astimezone(timezone.utc),
            submitted_before.astimezone(timezone.utc),
            announcement.astimezone(timezone.utc),
        )

    def build_url(
        self,
        *,
        start: int = 0,
        published_since: Optional[datetime] = None,
        published_before: Optional[datetime] = None,
    ) -> str:
        if not self.papers.categories:
            raise ValueError("At least one arXiv category must be configured")
        if not 1 <= self.papers.page_size <= 2000:
            raise ValueError("papers.page_size must be between 1 and 2000")
        if published_since is None or published_before is None:
            published_since, published_before, _ = self.announcement_window()
        if published_since.tzinfo is None or published_before.tzinfo is None:
            raise ValueError("arXiv publication window must be timezone-aware")
        published_since = published_since.astimezone(timezone.utc)
        published_before = published_before.astimezone(timezone.utc)
        if published_before <= published_since:
            raise ValueError("arXiv publication window must be non-empty")

        categories = " OR ".join(
            f"cat:{category}" for category in self.papers.categories
        )
        # submittedDate uses GMT minute precision and the upper bound is
        # inclusive. Convert the official half-open submission interval to the
        # API's inclusive representation without admitting the next batch.
        inclusive_end = published_before - timedelta(minutes=1)
        date_range = (
            f"submittedDate:[{published_since:%Y%m%d%H%M} "
            f"TO {inclusive_end:%Y%m%d%H%M}]"
        )
        # Do not pre-filter on MLLM/VLA/driving words here: every new paper in
        # the configured categories must reach the semantic triage stage.
        query = f"({categories}) AND {date_range}"
        params = {
            "search_query": query,
            "start": max(0, int(start)),
            "max_results": self.papers.page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        return f"{ARXIV_API}?{urlencode(params)}"

    def collect(self, now: Optional[datetime] = None) -> CollectionResult:
        started = time.monotonic()
        source_url = ""
        try:
            current = now or self._clock()
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            current = current.astimezone(timezone.utc)
            local_day_start, local_day_end = self.daily_window(current)
            published_since, published_before, announced_at = (
                self.announcement_window(current)
            )
            if not local_day_start <= announced_at < local_day_end:
                current_local = current.astimezone(ZoneInfo(self.timezone_name))
                if current_local.weekday() < 5:
                    source_url = ARXIV_API
                    raise RuntimeError(
                        "today's arXiv announcement is not available yet"
                    )
                return CollectionResult(
                    source_id="arxiv",
                    items=[],
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    source_name="arXiv",
                    source_url="https://arxiv.org/",
                    final_url="https://arxiv.org/",
                    http_status=200,
                    domain_match=True,
                )
            source_url = self.build_url(
                start=0,
                published_since=published_since,
                published_before=published_before,
            )
            items: List[RadarItem] = []
            start = 0
            total_results: Optional[int] = None
            final_url = ""
            http_status = 0
            domain_match = True
            page_number = 0

            while total_results is None or start < total_results:
                if page_number:
                    self._sleep(max(0.0, self.papers.page_delay_seconds))
                page_url = self.build_url(
                    start=start,
                    published_since=published_since,
                    published_before=published_before,
                )
                response = fetch_response(
                    page_url,
                    user_agent=self.network.user_agent,
                    timeout=self.network.timeout_seconds,
                    retries=self.network.retries,
                    retry_backoff_seconds=max(
                        3.0, self.network.retry_backoff_seconds
                    ),
                )
                final_url = response.final_url
                http_status = response.status
                page_domain_match = (
                    response.final_url.startswith("https://export.arxiv.org/")
                    or response.final_url.startswith("http://export.arxiv.org/")
                )
                domain_match = domain_match and page_domain_match
                if not page_domain_match:
                    raise RuntimeError("arXiv API redirected outside export.arxiv.org")

                page_items, page_total, entry_count = self.parse_page(
                    response.payload
                )
                if total_results is None:
                    total_results = page_total
                elif page_total is not None and page_total != total_results:
                    raise RuntimeError("arXiv result count changed during pagination")

                for item in page_items:
                    if (
                        published_since <= item.published_at < published_before
                    ):
                        first_submitted_at = item.published_at
                        item.metadata["is_new_submission"] = True
                        item.metadata["submission_type"] = "new-submission"
                        item.metadata["arxiv_first_submitted_at"] = (
                            first_submitted_at.isoformat()
                        )
                        item.metadata["announcement_batch_at"] = (
                            announced_at.isoformat()
                        )
                        item.metadata["announcement_batch_local_date"] = (
                            announced_at.astimezone(
                                ZoneInfo(self.timezone_name)
                            ).date().isoformat()
                        )
                        item.metadata["published_at_basis"] = (
                            "arxiv-scheduled-announcement"
                        )
                        item.metadata["collection_window"] = {
                            "submitted_since": published_since.isoformat(),
                            "submitted_before": published_before.isoformat(),
                            "announcement_at": announced_at.isoformat(),
                            "announcement_timezone": ARXIV_EASTERN_TIMEZONE,
                        }
                        # The app's daily publication timestamp represents the
                        # public announcement batch.  The immutable first
                        # submission time remains separately auditable above.
                        item.published_at = announced_at
                        items.append(item)

                if entry_count <= 0:
                    break
                next_start = start + entry_count
                if next_start <= start:
                    raise RuntimeError("arXiv pagination did not advance")
                if total_results is not None and next_start >= total_results:
                    break
                if total_results is None and entry_count < self.papers.page_size:
                    break
                start = next_start
                page_number += 1

            if total_results == 0 or not items:
                raise RuntimeError(
                    "today's arXiv announcement query returned no papers; "
                    "the search index may not be ready"
                )

            return CollectionResult(
                source_id="arxiv",
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name="arXiv",
                source_url=source_url,
                final_url=final_url,
                http_status=http_status,
                domain_match=domain_match,
            )
        except Exception as exc:
            return CollectionResult(
                source_id="arxiv",
                error=f"{exc.__class__.__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name="arXiv",
                source_url=source_url,
                domain_match=False,
            )

    @staticmethod
    def parse(payload: bytes) -> List[RadarItem]:
        return ArxivCollector.parse_page(payload)[0]

    @staticmethod
    def parse_page(
        payload: bytes,
    ) -> Tuple[List[RadarItem], Optional[int], int]:
        root = ET.fromstring(payload)
        now = datetime.now(timezone.utc)
        result: List[RadarItem] = []
        total_results: Optional[int] = None
        entry_count = 0
        for element in root.iter():
            if _local_name(element.tag) == "totalresults":
                try:
                    total_results = max(0, int("".join(element.itertext()).strip()))
                except ValueError:
                    total_results = None
                break
        for entry in root.iter():
            if _local_name(entry.tag) != "entry":
                continue
            entry_count += 1
            title = clean_html(_text(entry, "title"))
            abstract = clean_html(_text(entry, "summary"))[:16000]
            raw_id = _text(entry, "id")
            if not title or not raw_id:
                continue
            arxiv_id = raw_id.rstrip("/").rsplit("/", 1)[-1]
            arxiv_id_without_version = re.sub(r"v\d+$", "", arxiv_id)
            version_match = re.search(r"v(\d+)$", arxiv_id)
            version_number = int(version_match.group(1)) if version_match else 0
            url = f"https://arxiv.org/abs/{arxiv_id_without_version}"
            authors = []
            categories = []
            pdf_url = ""
            for child in list(entry):
                name = _local_name(child.tag)
                if name == "author":
                    author_name = _text(child, "name")
                    if author_name:
                        authors.append(clean_html(author_name))
                elif name == "category":
                    term = child.attrib.get("term", "").strip()
                    if term:
                        categories.append(term)
                elif name == "link" and child.attrib.get("type") == "application/pdf":
                    pdf_url = child.attrib.get("href", "")
            comment = clean_html(_text(entry, "comment"))
            journal_ref = clean_html(_text(entry, "journal_ref"))
            doi = clean_html(_text(entry, "doi"))
            published_raw = _text(entry, "published")
            published_at, published_at_verified = parse_datetime_with_status(
                published_raw, now
            )
            if not published_at_verified:
                continue
            updated_raw = _text(entry, "updated")
            updated_at, updated_at_verified = parse_datetime_with_status(
                updated_raw, now
            )
            code_match = re.search(r"https?://github\.com/[^\s)\]}>,]+", f"{abstract} {comment}")
            metadata = {
                "arxiv_version": arxiv_id,
                "arxiv_version_number": version_number,
                "record_version_type": (
                    "initial-version"
                    if updated_at_verified and published_at == updated_at
                    else "updated-version"
                ),
                "pdf_url": pdf_url,
                "comment": comment,
                "journal_ref": journal_ref,
                "doi": doi,
                "arxiv_id": arxiv_id_without_version,
                "source_record_url": url,
                "published_raw": published_raw,
                "published_at_verified": True,
                "updated_raw": updated_raw,
                "updated_at": updated_at.isoformat() if updated_at_verified else "",
                "updated_at_verified": updated_at_verified,
            }
            if code_match:
                metadata["code_url"] = code_match.group(0).rstrip(".")
            result.append(
                RadarItem(
                    kind="paper",
                    title=title,
                    url=url,
                    canonical_url=canonicalize_url(url),
                    source_id="arxiv",
                    source_name="arXiv",
                    source_tier=1,
                    source_type="paper-api",
                    source_focus=1.0,
                    published_at=published_at,
                    summary=abstract,
                    external_id=arxiv_id_without_version,
                    authors=unique_preserving_order(authors),
                    categories=unique_preserving_order(categories),
                    tags=[],
                    fingerprint=fingerprint_title(title),
                    cluster_key=arxiv_id_without_version,
                    metadata=metadata,
                )
            )
        return result, total_results, entry_count
