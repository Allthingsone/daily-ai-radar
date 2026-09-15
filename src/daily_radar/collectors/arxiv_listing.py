from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Callable, Dict, List, Tuple
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..config import NetworkSettings, PaperSettings
from ..models import CollectionResult, RadarItem
from ..processing.normalize import fingerprint_title, unique_preserving_order
from .base import FetchResponse


LISTING_PAGE_SIZE = 2000
_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})"
_CATEGORY = r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)?"
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _ListingHTML(HTMLParser):
    """Build a small tree using the standard library's tolerant HTML parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = ET.SubElement(self.stack[-1], tag, {key: value or "" for key, value in attrs})
        if tag not in _VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        element = self.stack[-1]
        if len(element):
            element[-1].tail = (element[-1].tail or "") + data
        else:
            element.text = (element.text or "") + data


def _text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _class_element(element: ET.Element, class_name: str):
    return next((child for child in element.iter() if class_name in child.get("class", "").split()), None)


def _field(element: ET.Element, class_name: str, label: str = "") -> str:
    child = _class_element(element, class_name)
    text = _text(child) if child is not None else ""
    return text.removeprefix(label).strip()


@dataclass
class ListingEntry:
    identifier: str
    section: str
    title: str
    summary: str
    authors: List[str]
    categories: List[str]
    primary_category: str
    comment: str = ""


@dataclass
class ListingPage:
    listing_date: date
    total_entries: int
    entries: List[ListingEntry]


def parse_listing_page(payload: bytes) -> ListingPage:
    parser = _ListingHTML()
    parser.feed(payload.decode("utf-8"))
    parser.close()
    root = parser.root
    dates = [
        _text(heading).removeprefix("Showing new listings for ")
        for heading in root.iter("h3")
        if _text(heading).startswith("Showing new listings for ")
    ]
    if len(dates) != 1:
        raise ValueError("arXiv listing is missing its announcement date")
    listing_date = datetime.strptime(dates[0], "%A, %d %B %Y").date()
    paging = _class_element(root, "paging")
    total_match = re.search(r"Total of (\d+) entries", _text(paging) if paging is not None else "")
    if not total_match:
        raise ValueError("arXiv listing is missing its total entry count")
    total_entries = int(total_match.group(1))
    # The live site repeats id="articles" for each submission section.
    article_lists = root.findall(".//dl[@id='articles']")
    if not article_lists:
        if total_entries == 0:
            return ListingPage(listing_date, 0, [])
        raise ValueError("arXiv listing is missing its entries")

    section = ""
    section_expected = 0
    section_count = 0
    pending_id = ""
    entries = []
    for element in (child for articles in article_lists for child in articles):
        if element.tag == "h3":
            if pending_id or section_count != section_expected:
                raise ValueError("arXiv listing section is incomplete")
            heading = _text(element)
            match = re.fullmatch(
                r"(New submissions|Cross submissions|Cross-lists|Replacement submissions|Replacements)"
                r" \(showing (\d+) of (\d+) entries\)", heading,
            )
            if not match:
                raise ValueError("arXiv listing has an unrecognized entry section")
            section = {
                "New submissions": "new", "Cross submissions": "cross", "Cross-lists": "cross",
                "Replacement submissions": "replace", "Replacements": "replace",
            }[match[1]]
            section_expected, section_count = int(match[2]), 0
            if not section_expected <= int(match[3]) <= total_entries:
                raise ValueError("arXiv listing section count is inconsistent")
        elif element.tag == "dt":
            if pending_id:
                raise ValueError("arXiv listing has an entry without metadata")
            links = [link for link in element.iter("a") if link.get("title") == "Abstract"]
            if len(links) != 1 or not section:
                raise ValueError("arXiv listing has no unambiguous abstract record")
            link = links[0]
            url = urlsplit(urljoin("https://arxiv.org", link.get("href", "")))
            match = re.fullmatch(r"/abs/(" + _ID + r")(?:v\d+)?", url.path)
            label = re.fullmatch(r"arXiv:(" + _ID + r")(?:v\d+)?", _text(link))
            if (url.scheme != "https" or url.netloc != "arxiv.org" or url.query or url.fragment
                    or not match or not label or match[1] != label[1]):
                raise ValueError("arXiv listing ID does not match its official abstract URL")
            pending_id = match[1]
        elif element.tag == "dd":
            if not pending_id:
                raise ValueError("arXiv listing metadata has no matching ID")
            title = _field(element, "list-title", "Title:")
            paragraphs = [child for child in element.iter("p") if "mathjax" in child.get("class", "").split()]
            summary = " ".join(_text(child) for child in paragraphs).strip()
            subjects = _field(element, "list-subjects", "Subjects:")
            categories = unique_preserving_order(re.findall(r"\((" + _CATEGORY + r")\)", subjects))
            primary = re.findall(r"\((" + _CATEGORY + r")\)", _field(element, "primary-subject"))
            authors_element = _class_element(element, "list-authors")
            authors = [_text(link) for link in authors_element.iter("a")] if authors_element is not None else []
            if section in {"new", "cross"} and (not title or not summary or len(primary) != 1 or not authors):
                raise ValueError("arXiv listing entry lacks a title, full abstract, authors or primary category")
            entries.append(ListingEntry(
                pending_id, section, title, summary, authors, categories,
                primary[0] if primary else "", _field(element, "list-comments", "Comments:"),
            ))
            section_count += 1
            pending_id = ""
    if pending_id or section_count != section_expected or len(entries) > total_entries:
        raise ValueError("arXiv listing entry count is inconsistent")
    return ListingPage(listing_date, total_entries, entries)


class ArxivListingCollector:
    """Read complete, dated official lists without depending on the search API.

    Cross-lists can be old papers. A cross-listed candidate is accepted only
    when its primary category also lists it as a new submission on this date.
    """

    def __init__(self, papers: PaperSettings, network: NetworkSettings,
                 timezone_name: str, fetcher: Callable[..., FetchResponse],
                 sleeper: Callable[[float], None]) -> None:
        self.papers = papers
        self.network = network
        self.timezone_name = timezone_name
        self.fetcher = fetcher
        self.sleeper = sleeper
        self.page_urls: List[str] = []

    def _category(self, category: str, expected_date: date) -> Tuple[List[ListingEntry], str]:
        if not re.fullmatch(_CATEGORY, category):
            raise ValueError("Invalid arXiv listing category")
        source_url = f"https://arxiv.org/list/{category}/new"
        entries: List[ListingEntry] = []
        seen = set()
        total = None
        while total is None or len(entries) < total:
            self.sleeper(max(3.0, self.papers.page_delay_seconds))
            url = source_url + "?" + urlencode({"skip": len(entries), "show": LISTING_PAGE_SIZE})
            response = self.fetcher(
                url, user_agent=self.network.user_agent,
                timeout=self.network.timeout_seconds, retries=self.network.retries,
                retry_backoff_seconds=max(3.0, self.network.retry_backoff_seconds),
                rate_limit_backoff_seconds=self.papers.rate_limit_backoff_seconds,
                sleeper=self.sleeper,
            )
            final = urlsplit(response.final_url)
            allowed_categories = {category, "eess.SY" if category == "cs.SY" else category}
            if (response.status != 200 or final.scheme != "https" or final.netloc != "arxiv.org"
                    or final.path not in {f"/list/{value}/new" for value in allowed_categories}):
                raise ValueError("arXiv listing did not return the requested official category")
            page = parse_listing_page(response.payload)
            if page.listing_date != expected_date:
                raise ValueError(f"arXiv listing date {page.listing_date} does not match expected {expected_date}")
            if total is None:
                total = page.total_entries
            elif total != page.total_entries:
                raise ValueError("arXiv listing count changed during pagination")
            if total and not page.entries:
                raise ValueError("arXiv listing pagination ended before the advertised total")
            for entry in page.entries:
                if entry.identifier in seen:
                    raise ValueError("arXiv listing pagination repeated a paper")
                seen.add(entry.identifier)
                entries.append(entry)
            if len(entries) > total:
                raise ValueError("arXiv listing pagination exceeded its advertised total")
            self.page_urls.append(response.final_url)
        return entries, source_url

    def collect(self, announced_at: datetime) -> CollectionResult:
        if announced_at.tzinfo is None:
            raise ValueError("arXiv announcement timestamp must be timezone-aware")
        self.page_urls = []
        expected_date = announced_at.astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=1)
        categories = unique_preserving_order(self.papers.categories)
        if not categories:
            raise ValueError("At least one arXiv category must be configured")
        pages: Dict[str, Tuple[List[ListingEntry], str]] = {}
        eligible: Dict[str, ListingEntry] = {}
        for category in categories:
            pages[category] = self._category(category, expected_date)
            for entry in pages[category][0]:
                if entry.section in {"new", "cross"}:
                    eligible[entry.identifier] = entry
        primary_categories = sorted({
            entry.primary_category for entry in eligible.values()
            if entry.section == "cross" and entry.primary_category not in pages
        })
        for category in primary_categories:
            pages[category] = self._category(category, expected_date)
        new_entries = {
            entry.identifier: (entry, source_url)
            for entries, source_url in pages.values()
            for entry in entries if entry.section == "new"
        }
        items = []
        local_date = announced_at.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()
        for identifier in sorted(eligible.keys() & new_entries.keys()):
            entry, proof_url = new_entries[identifier]
            url = f"https://arxiv.org/abs/{identifier}"
            metadata = {
                "arxiv_id": identifier, "source_record_url": url,
                "collection_method": "arxiv-new-list",
                "announcement_listing_url": proof_url,
                "announcement_listing_date": expected_date.isoformat(),
                "announcement_type": "new", "primary_category": entry.primary_category,
                "published_at_verified": True,
                "published_raw": expected_date.isoformat(),
                "published_at_basis": "arxiv-official-new-list",
                "is_new_submission": True, "submission_type": "new-submission",
                "announcement_batch_at": announced_at.isoformat(),
                "announcement_batch_local_date": local_date,
                "pdf_url": f"https://arxiv.org/pdf/{identifier}", "comment": entry.comment,
            }
            code = re.search(r"https?://github\.com/[^\s)\]}>,]+", entry.summary + " " + entry.comment)
            if code:
                metadata["code_url"] = code[0].rstrip(".")
            items.append(RadarItem(
                kind="paper", title=entry.title, summary=entry.summary,
                url=url, canonical_url=url, external_id=identifier,
                source_id="arxiv", source_name="arXiv", source_tier=1,
                source_type="paper-list", source_focus=1.0, published_at=announced_at,
                authors=unique_preserving_order(entry.authors),
                categories=unique_preserving_order(entry.categories + eligible[identifier].categories),
                fingerprint=fingerprint_title(entry.title), cluster_key=identifier, metadata=metadata,
            ))
        return CollectionResult(
            source_id="arxiv", source_name="arXiv", items=items,
            source_url=pages[categories[0]][1], final_url=self.page_urls[0],
            http_status=200, domain_match=True,
            details={
                "collection_method": "arxiv-new-list", "announcement_status": "available",
                "announcement_listing_date": expected_date.isoformat(),
                "listing_categories": categories, "cross_list_primary_categories": primary_categories,
                "listing_pages": len(self.page_urls), "listing_urls": self.page_urls,
                "listing_candidate_ids": len(eligible), "verified_new_listing_ids": len(items),
                "old_cross_lists_excluded": len(eligible.keys() - new_entries.keys()),
            },
        )
