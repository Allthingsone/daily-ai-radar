from __future__ import annotations

import gzip
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RedirectHandler(HTTPRedirectHandler):
    # Python 3.9's default urllib handler predates consistent HTTP 308 support.
    http_error_308 = HTTPRedirectHandler.http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code == 308:
            code = 307  # Both preserve the method for GET/HEAD requests.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class FetchResponse:
    payload: bytes
    final_url: str
    status: int
    content_type: str


class RetryDeferred(RuntimeError):
    """The server requested a cooldown longer than this attempt can wait."""


def retry_after_seconds(value: str, now: Optional[datetime] = None) -> float:
    value = (value or "").strip()
    if value.isdigit():
        return float(value)
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(0.0, (deadline - (now or datetime.now(timezone.utc))).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def build_http_opener():
    return build_opener(_RedirectHandler())


def fetch_response(
    url: str,
    user_agent: str,
    timeout: int,
    retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    rate_limit_backoff_seconds: float = 0.0,
    max_retry_wait_seconds: float = 60.0,
    sleeper: Optional[Callable[[float], None]] = None,
) -> FetchResponse:
    last_error = None
    for attempt in range(max(0, retries) + 1):
        request = Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*",
                "Accept-Encoding": "gzip",
            },
        )
        opener = build_http_opener()
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = response.read()
                encoding = response.headers.get("Content-Encoding", "").lower()
                final_url = response.geturl()
                status = int(getattr(response, "status", 200) or 200)
                content_type = response.headers.get("Content-Type", "")
            return FetchResponse(
                payload=gzip.decompress(payload) if encoding == "gzip" else payload,
                final_url=final_url,
                status=status,
                content_type=content_type,
            )
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < retries:
            delay = max(0.0, retry_backoff_seconds) * (2**attempt)
            if isinstance(last_error, HTTPError):
                if last_error.code == 429:
                    delay = max(delay, rate_limit_backoff_seconds * (2**attempt))
                delay = max(
                    delay,
                    retry_after_seconds((last_error.headers or {}).get("Retry-After", "")),
                )
            if delay > max_retry_wait_seconds:
                raise RetryDeferred(
                    f"Retry deferred: requested cooldown is {delay:.0f} seconds; "
                    "no early retry was sent"
                ) from last_error
            (sleeper or time.sleep)(delay)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")


def fetch_bytes(url: str, user_agent: str, timeout: int) -> bytes:
    return fetch_response(url, user_agent, timeout).payload
