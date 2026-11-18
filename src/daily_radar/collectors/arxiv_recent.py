from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..models import CollectionResult, RadarItem
from ..processing.normalize import unique_preserving_order
from .arxiv_listing import (
    LISTING_PAGE_SIZE, ListingEntry, _CATEGORY, _ID, _ListingHTML,
    _class_element, _field, _text,
)


METADATA_BATCH_SIZE = 50


@dataclass
class RecentDay:
    day: date
    total: int
    entries: List[ListingEntry]


@dataclass
class RecentPage:
    total: int
    days: List[RecentDay]


def parse_recent_page(payload: bytes) -> RecentPage:
    """Parse dated recent listings, including pagination across day boundaries.

    Unlike /new, /recent has dates per section and no abstracts. Its cross-list
    marker is on the ID row. An HTML link to v2 does not mean a replacement:
    the record may have been revised since its original announcement.
    """
    parser = _ListingHTML()
    parser.feed(payload.decode("utf-8"))
    parser.close()
    root = parser.root
    paging = _class_element(root, "paging")
    total = re.search(r"Total of (\d+) entries", _text(paging) if paging is not None else "")
    if not total:
        raise ValueError("arXiv recent listing is missing its total entry count")
    days: List[RecentDay] = []
    current = None
    shown = 0
    pending = None
    for articles in root.findall(".//dl[@id='articles']"):
        for index, element in enumerate(articles):
            if element.tag == "h3":
                if pending or (current is not None and len(current.entries) != shown):
                    raise ValueError("arXiv recent day is incomplete")
                match = re.fullmatch(
                    r"([A-Za-z]+, \d{1,2} [A-Za-z]+ \d{4}) \(showing (?:(?:first|last) )?(\d+) of (\d+) entries\s*\)",
                    _text(element),
                )
                empty_day = re.fullmatch(r"[A-Za-z]+, \d{1,2} [A-Za-z]+ \d{4}", _text(element))
                confirmed_empty = bool(empty_day and index + 1 < len(articles)
                                       and articles[index + 1].tag == "p"
                                       and _text(articles[index + 1]) == "No updates for this time period.")
                if not match and not confirmed_empty:
                    raise ValueError(f"arXiv recent listing has an unrecognized dated section: {_text(element)}")
                day = datetime.strptime(match[1] if match else empty_day[0], "%a, %d %b %Y").date()
                shown, day_total = (int(match[2]), int(match[3])) if match else (0, 0)
                if not 0 <= shown <= day_total <= int(total[1]) or (days and day >= days[-1].day):
                    raise ValueError("arXiv recent dates or counts are inconsistent")
                current = RecentDay(day, day_total, [])
                days.append(current)
            elif element.tag == "dt":
                if pending or current is None:
                    raise ValueError("arXiv recent record has no unambiguous date")
                links = [link for link in element.iter("a") if link.get("title") == "Abstract"]
                if len(links) != 1:
                    raise ValueError("arXiv recent record has no unambiguous ID")
                link = links[0]
                url = urlsplit(urljoin("https://arxiv.org", link.get("href", "")))
                identifier = re.fullmatch(r"/abs/(" + _ID + r")(?:v\d+)?", url.path)
                label = re.fullmatch(r"arXiv:(" + _ID + r")(?:v\d+)?", _text(link))
                if (url.scheme != "https" or url.netloc != "arxiv.org" or url.query or url.fragment
                        or not identifier or not label or identifier[1] != label[1]):
                    raise ValueError("arXiv recent ID does not match its official abstract URL")
                cross = re.search(r"\(cross-list from (" + _CATEGORY + r")\)", _text(element))
                section = "replace" if "(replaced)" in _text(element) else ("cross" if cross else "new")
                pending = (identifier[1], section, cross[1] if cross else "")
            elif element.tag == "dd":
                if not pending or current is None:
                    raise ValueError("arXiv recent metadata has no matching ID")
                title = _field(element, "list-title", "Title:")
                categories = re.findall(r"\((" + _CATEGORY + r")\)", _field(element, "list-subjects"))
                primary = re.findall(r"\((" + _CATEGORY + r")\)", _field(element, "primary-subject"))
                authors_element = _class_element(element, "list-authors")
                authors = [_text(link) for link in authors_element.iter("a")] if authors_element is not None else []
                if not title or not authors or len(primary) != 1 or (pending[2] and pending[2] != primary[0]):
                    raise ValueError("arXiv recent metadata is incomplete or inconsistent")
                current.entries.append(ListingEntry(
                    pending[0], pending[1], title, "", authors, categories, primary[0],
                    _field(element, "list-comments", "Comments:"),
                ))
                pending = None
    if pending or not days or len(days[-1].entries) != shown:
        raise ValueError("arXiv recent listing has no complete dated sections")
    if sum(len(day.entries) for day in days) > int(total[1]):
        raise ValueError("arXiv recent listing exceeds its advertised total")
    return RecentPage(int(total[1]), days)


class ArxivRecentCollector:
    """Resolve announcement membership first, then fetch the exact v1 records."""

    def __init__(self, papers, network, timezone_name: str, fetcher: Callable,
                 sleeper: Callable, parse_metadata: Callable) -> None:
        self.papers = papers
        self.network = network
        self.timezone_name = timezone_name
        self.fetcher = fetcher
        self.sleeper = sleeper
        self.parse_metadata = parse_metadata
        self.page_urls: List[str] = []
        self.current_url = ""

    def _fetch(self, url: str, *, metadata: bool = False):
        self.current_url = url
        self.sleeper(max(3.0, self.papers.page_delay_seconds))
        return self.fetcher(
            url, user_agent=self.network.user_agent,
            # Historical id_list queries can take longer than an HTML listing.
            # This allowance is paper-only; news keeps its existing timeout.
            timeout=max(60, self.network.timeout_seconds) if metadata else self.network.timeout_seconds,
            retries=self.network.retries,
            retry_backoff_seconds=max(3.0, self.network.retry_backoff_seconds),
            rate_limit_backoff_seconds=self.papers.rate_limit_backoff_seconds,
            sleeper=self.sleeper,
        )

    def _category(self, category: str, target: date) -> List[ListingEntry]:
        if not re.fullmatch(_CATEGORY, category):
            raise ValueError("Invalid arXiv listing category")
        source_url = f"https://arxiv.org/list/{category}/recent"
        offset, total = 0, None
        seen = set()
        days: Dict[date, RecentDay] = {}
        while total is None or offset < total:
            response = self._fetch(source_url + "?" + urlencode({"skip": offset, "show": LISTING_PAGE_SIZE}))
            final = urlsplit(response.final_url)
            aliases = {category, "eess.SY" if category == "cs.SY" else category}
            if (response.status != 200 or final.scheme != "https" or final.netloc != "arxiv.org"
                    or final.path not in {f"/list/{value}/recent" for value in aliases}):
                raise ValueError("arXiv recent listing did not return the requested official category")
            try:
                page = parse_recent_page(response.payload)
            except ValueError as exc:
                raise ValueError(f"{category}: {exc}") from exc
            if total is not None and total != page.total:
                raise ValueError("arXiv recent result count changed during pagination")
            total = page.total
            count = sum(len(day.entries) for day in page.days)
            if not count and offset < total:
                raise ValueError("arXiv recent pagination ended early")
            if offset + count > total or (days and page.days[0].day > min(days)):
                raise ValueError("arXiv recent pagination order or count is inconsistent")
            for day in page.days:
                accumulated = days.setdefault(day.day, RecentDay(day.day, day.total, []))
                if accumulated.total != day.total:
                    raise ValueError("arXiv announcement count changed during pagination")
                for entry in day.entries:
                    if entry.identifier in seen:
                        raise ValueError("arXiv recent pagination repeated a paper")
                    # An unmarked record must belong to this primary category.
                    if entry.section == "new" and entry.primary_category not in aliases:
                        raise ValueError("arXiv recent entry lacks its cross-list marker")
                    seen.add(entry.identifier)
                    accumulated.entries.append(entry)
                if len(accumulated.entries) > accumulated.total:
                    raise ValueError("arXiv announcement exceeds its advertised total")
            self.page_urls.append(response.final_url)
            offset += count
            if target in days and len(days[target].entries) == days[target].total:
                return days[target].entries
            if min(days) < target:
                # Missing headings are empty only when complete dated sections
                # bracket the target. A stale /recent page is never an empty day.
                if target not in days and max(days) > target:
                    return []
                break
        raise ValueError(f"arXiv recent listing does not contain a complete {target} batch for {category}")

    def collect(self, announced_at: datetime) -> CollectionResult:
        if announced_at.tzinfo is None:
            raise ValueError("arXiv announcement timestamp must be timezone-aware")
        target = announced_at.astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=1)
        categories = unique_preserving_order(self.papers.categories)
        if not categories or not 1 <= self.papers.page_size <= 2000:
            raise ValueError("Configure arXiv categories and a page size between 1 and 2000")
        self.page_urls = []
        pages = {category: self._category(category, target) for category in categories}
        eligible = {entry.identifier: entry for entries in pages.values() for entry in entries
                    if entry.section in {"new", "cross"}}
        primary_categories = sorted({entry.primary_category for entry in eligible.values()
                                     if entry.section == "cross" and entry.primary_category not in pages})
        for category in primary_categories:
            pages[category] = self._category(category, target)
        new_entries = {entry.identifier: (entry, category) for category, entries in pages.items()
                       for entry in entries if entry.section == "new"}
        identifiers = sorted(eligible.keys() & new_entries.keys())
        items: List[RadarItem] = []
        metadata_urls = []
        # Version 1 avoids silently screening revisions made after the target
        # announcement. Submission timestamps are retained, not used as membership.
        batch_size = min(METADATA_BATCH_SIZE, self.papers.page_size)
        for start in range(0, len(identifiers), batch_size):
            batch = identifiers[start:start + batch_size]
            url = "https://export.arxiv.org/api/query?" + urlencode({
                "id_list": ",".join(identifier + "v1" for identifier in batch),
                "max_results": len(batch),
            })
            response = self._fetch(url, metadata=True)
            final = urlsplit(response.final_url)
            if (response.status != 200 or final.scheme != "https"
                    or final.netloc != "export.arxiv.org" or final.path != "/api/query"):
                raise ValueError("arXiv metadata did not return the requested official API")
            records, total, count = self.parse_metadata(response.payload)
            if (total != len(batch) or count != len(batch) or len(records) != len(batch)
                    or {item.external_id for item in records} != set(batch)):
                raise ValueError("arXiv metadata response is missing or repeating requested records")
            metadata_urls.append(response.final_url)
            for item in records:
                entry, primary = new_entries[item.external_id]
                if (item.metadata.get("arxiv_version_number") != 1 or not item.summary
                        or not item.authors or not item.categories or item.published_at > announced_at):
                    raise ValueError("arXiv initial-version metadata is incomplete or inconsistent")
                item.metadata.update({
                    "collection_method": "arxiv-recent-list",
                    "announcement_listing_url": f"https://arxiv.org/list/{primary}/recent",
                    "announcement_listing_date": target.isoformat(),
                    "announcement_type": "new", "primary_category": entry.primary_category,
                    "arxiv_first_submitted_at": item.published_at.isoformat(),
                    "published_at_basis": "arxiv-official-recent-list",
                    "is_new_submission": True, "submission_type": "new-submission",
                    "announcement_batch_at": announced_at.isoformat(),
                    "announcement_batch_local_date": announced_at.astimezone(ZoneInfo(self.timezone_name)).date().isoformat(),
                    "metadata_source_url": response.final_url,
                })
                item.published_at = announced_at
                item.source_type = "paper-list"
                item.categories = unique_preserving_order(item.categories + eligible[item.external_id].categories)
                items.append(item)
        return CollectionResult(
            source_id="arxiv", source_name="arXiv", items=items,
            source_url=f"https://arxiv.org/list/{categories[0]}/recent", final_url=self.page_urls[0],
            http_status=200, domain_match=True,
            details={
                "collection_method": "arxiv-recent-list", "announcement_status": "available",
                "announcement_listing_date": target.isoformat(),
                "listing_categories": categories, "cross_list_primary_categories": primary_categories,
                "listing_pages": len(self.page_urls), "listing_urls": self.page_urls,
                "listing_candidate_ids": len(eligible), "verified_new_listing_ids": len(items),
                "old_cross_lists_excluded": len(eligible.keys() - new_entries.keys()),
                "api_pages": len(metadata_urls), "metadata_urls": metadata_urls,
                "empty_reason": "no_new_submissions" if not items else "",
            },
        )
