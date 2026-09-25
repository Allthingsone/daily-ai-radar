"""Preserve published archives and publish historical digests without dating them as today."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import List
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .collectors.base import fetch_response
from .config import Settings
from .static_site import build_static_site
from .time_windows import digest_reference


SITE_FILES = ("index.html", "data/latest.json", "daily.md", "feed.xml", "assets/pages.css", "assets/pages.js")


def _dates(payload: object) -> List[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), list):
        raise ValueError("Invalid published archive manifest")
    days = payload["dates"]
    if len(days) > 3660:
        raise ValueError("Archive manifest is unexpectedly large")
    for day in days:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("Invalid archive date")
    return sorted(set(days))


def restore_published_site(settings: Settings, site_url: str, output: Path, include_root: bool = False) -> int:
    """Fail closed on an unavailable archive: a deploy must never erase it."""
    origin = urlsplit(site_url)
    if origin.scheme != "https" or not origin.netloc or origin.query or origin.fragment:
        raise ValueError("Published site must be an HTTPS directory URL")
    base = site_url.rstrip("/") + "/"

    def fetch(relative: str) -> bytes:
        response = fetch_response(
            base + relative, user_agent=settings.network.user_agent,
            timeout=settings.network.timeout_seconds, retries=settings.network.retries,
            retry_backoff_seconds=settings.network.retry_backoff_seconds,
        )
        final = urlsplit(response.final_url)
        expected = urlsplit(base + relative)
        if response.status != 200 or (final.scheme, final.netloc, final.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError("Published archive redirected outside the requested site file")
        return response.payload

    try:
        manifest = fetch("archive/index.json")
    except HTTPError as exc:
        if exc.code != 404:
            raise
        days = []
    else:
        days = _dates(json.loads(manifest))
    prefixes = ([""] if include_root else []) + [f"archive/{day}/" for day in days]
    for prefix in prefixes:
        for filename in SITE_FILES:
            content = fetch(prefix + filename)
            path = output / prefix / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    output.mkdir(parents=True, exist_ok=True)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    if days:
        path = output / "archive" / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"dates": days}), encoding="utf-8")
    return len(days)


def add_archive_links(output: Path) -> None:
    manifest = output / "archive" / "index.json"
    if not manifest.exists():
        return
    days = _dates(json.loads(manifest.read_text(encoding="utf-8")))
    index = output / "index.html"
    content = index.read_text(encoding="utf-8")
    content = re.sub(r"<!-- radar-archives:start -->.*?<!-- radar-archives:end -->", "", content, flags=re.S)
    if "</body>" not in content:
        raise ValueError("Published root page has no body end; refusing to overwrite it")
    links = " · ".join(f'<a href="archive/{day}/">{day}</a>' for day in reversed(days))
    block = f'<!-- radar-archives:start --><aside style="padding:24px;text-align:center">历史日报：{links}</aside><!-- radar-archives:end -->'
    index.write_text(content.replace("</body>", block + "\n</body>"), encoding="utf-8")


def build_archive(settings: Settings, output: Path, database, site_url: str, target_date: str) -> List[Path]:
    digest_reference(target_date, settings.timezone)
    if not (output / "index.html").is_file():
        raise ValueError("Restore the published root site before building a historical archive")
    target = output / "archive" / target_date
    paths = build_static_site(
        settings, target, database=database,
        site_url=site_url.rstrip("/") + f"/archive/{target_date}/",
        target_date=target_date,
    )
    manifest = output / "archive" / "index.json"
    days = _dates(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else []
    manifest.write_text(json.dumps({"dates": sorted(set(days + [target_date]))}), encoding="utf-8")
    add_archive_links(output)
    return paths + [manifest]
