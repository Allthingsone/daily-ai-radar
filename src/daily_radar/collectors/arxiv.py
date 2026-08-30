from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlencode

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


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(entry: ET.Element, name: str) -> str:
    for child in list(entry):
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


class ArxivCollector:
    def __init__(self, papers: PaperSettings, network: NetworkSettings) -> None:
        self.papers = papers
        self.network = network

    def build_url(self) -> str:
        categories = " OR ".join(
            f"cat:{category}" for category in self.papers.categories
        )
        # Server-side candidate generation prevents the broad cs.CV/cs.LG
        # firehose from filling max_results before any driving paper appears.
        # The stricter local two-axis gate remains the final authority.
        model_terms = " OR ".join(
            (
                'all:"vision-language"',
                'all:"vision language"',
                'all:"multimodal large language"',
                "all:MLLM",
                "all:VLM",
                "all:VLA",
            )
        )
        driving_terms = " OR ".join(
            (
                'all:"autonomous driving"',
                'all:"self-driving"',
                'all:"driving agent"',
                'all:"driving scene"',
                'all:"driving policy"',
                "all:driving",
            )
        )
        query = f"({categories}) AND ({model_terms}) AND ({driving_terms})"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": self.papers.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        return f"{ARXIV_API}?{urlencode(params)}"

    def collect(self) -> CollectionResult:
        started = time.monotonic()
        try:
            response = fetch_response(
                self.build_url(),
                user_agent=self.network.user_agent,
                timeout=self.network.timeout_seconds,
                retries=self.network.retries,
                retry_backoff_seconds=self.network.retry_backoff_seconds,
            )
            items = self.parse(response.payload)
            return CollectionResult(
                source_id="arxiv",
                items=items,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name="arXiv",
                source_url=self.build_url(),
                final_url=response.final_url,
                http_status=response.status,
                domain_match=(
                    response.final_url.startswith("https://export.arxiv.org/")
                    or response.final_url.startswith("http://export.arxiv.org/")
                ),
            )
        except Exception as exc:
            return CollectionResult(
                source_id="arxiv",
                error=f"{exc.__class__.__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                source_name="arXiv",
                source_url=self.build_url(),
                domain_match=False,
            )

    @staticmethod
    def parse(payload: bytes) -> List[RadarItem]:
        root = ET.fromstring(payload)
        now = datetime.now(timezone.utc)
        result: List[RadarItem] = []
        for entry in root.iter():
            if _local_name(entry.tag) != "entry":
                continue
            title = clean_html(_text(entry, "title"))
            abstract = clean_html(_text(entry, "summary"))[:16000]
            raw_id = _text(entry, "id")
            if not title or not raw_id:
                continue
            arxiv_id = raw_id.rstrip("/").rsplit("/", 1)[-1]
            arxiv_id_without_version = re.sub(r"v\d+$", "", arxiv_id)
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
            code_match = re.search(r"https?://github\.com/[^\s)\]}>,]+", f"{abstract} {comment}")
            metadata = {
                "arxiv_version": arxiv_id,
                "pdf_url": pdf_url,
                "comment": comment,
                "journal_ref": journal_ref,
                "doi": doi,
                "arxiv_id": arxiv_id_without_version,
                "source_record_url": url,
                "published_raw": published_raw,
                "published_at_verified": True,
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
        return result
