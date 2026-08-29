from __future__ import annotations

import gzip
import time
from dataclasses import dataclass
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


def build_http_opener():
    return build_opener(_RedirectHandler())


def fetch_response(
    url: str,
    user_agent: str,
    timeout: int,
    retries: int = 0,
    retry_backoff_seconds: float = 1.0,
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
            time.sleep(max(0.0, retry_backoff_seconds) * (2**attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")


def fetch_bytes(url: str, user_agent: str, timeout: int) -> bytes:
    return fetch_response(url, user_agent, timeout).payload
