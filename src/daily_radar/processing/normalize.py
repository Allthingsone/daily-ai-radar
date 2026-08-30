from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
    "spm",
}

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def clean_html(value: str) -> str:
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def canonicalize_url(value: str) -> str:
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.scheme and parts.path:
        parts = urlsplit("https://" + value)

    scheme = parts.scheme.lower() or "https"
    hostname = (parts.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = parts.port
    netloc = hostname
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if hostname in {"arxiv.org", "export.arxiv.org"}:
        match = re.search(r"/(?:abs|pdf)/([^/?#]+)", path)
        if match:
            arxiv_id = re.sub(r"\.pdf$", "", match.group(1), flags=re.I)
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
            return f"https://arxiv.org/abs/{arxiv_id}"

    if path != "/":
        path = path.rstrip("/")
    query_pairs = []
    for key, val in parse_qsl(parts.query, keep_blank_values=False):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_PARAMS:
            continue
        query_pairs.append((key, val))
    query_pairs.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_pairs), ""))


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean_html(value)).lower()
    value = re.sub(r"[^\w\s\u4e00-\u9fff-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_tokens(value: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalize_title(value))
    return [
        _stem_token(token)
        for token in tokens
        if token not in STOP_WORDS and len(token) > 1
    ]


def _stem_token(token: str) -> str:
    """Normalize common headline inflections without a heavyweight NLP dependency."""
    if not token.isascii() or not token.isalpha():
        return token
    if len(token) > 6 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 5 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 5 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def fingerprint_title(value: str) -> str:
    normalized = " ".join(title_tokens(value)) or normalize_title(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def title_similarity(left: str, right: str) -> float:
    left_normal = normalize_title(left)
    right_normal = normalize_title(right)
    if not left_normal or not right_normal:
        return 0.0
    if left_normal == right_normal:
        return 1.0
    left_set = set(title_tokens(left))
    right_set = set(title_tokens(right))
    if len(left_set) < 3 or len(right_set) < 3:
        return SequenceMatcher(None, left_normal, right_normal).ratio()
    union = left_set | right_set
    jaccard = len(left_set & right_set) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_normal, right_normal).ratio()
    return 0.7 * jaccard + 0.3 * sequence


def parse_datetime_with_status(
    value: str, fallback: datetime = None
) -> Tuple[datetime, bool]:
    fallback = fallback or datetime.now(timezone.utc)
    if not value:
        return fallback, False
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc), True
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc), True
    except ValueError:
        return fallback, False


def parse_datetime(value: str, fallback: datetime = None) -> datetime:
    return parse_datetime_with_status(value, fallback)[0]


def unique_preserving_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result
