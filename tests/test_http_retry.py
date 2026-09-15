import unittest
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from daily_radar.collectors.base import RetryDeferred, fetch_response, retry_after_seconds


class HTTPRetryTests(unittest.TestCase):
    def error(self, code=429, retry_after=""):
        headers = Message()
        if retry_after:
            headers["Retry-After"] = retry_after
        return HTTPError("https://export.arxiv.org/api/query", code, "busy", headers, BytesIO())

    def response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"complete response"
        response.headers = {}
        response.geturl.return_value = "https://export.arxiv.org/api/query"
        response.status = 200
        return response

    def test_rate_limited_requests_back_off_and_recover(self):
        waits = []
        opener = MagicMock()
        opener.open.side_effect = [self.error(), self.error(), self.response()]
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            result = fetch_response("https://export.arxiv.org/api/query", "test", 25,
                                    retries=2, retry_backoff_seconds=3,
                                    rate_limit_backoff_seconds=30, sleeper=waits.append)
        self.assertEqual(waits, [30, 60])
        self.assertEqual(result.payload, b"complete response")
        self.assertEqual(opener.open.call_count, 3)

    def test_retry_after_is_honored_for_service_unavailable(self):
        waits = []
        opener = MagicMock()
        opener.open.side_effect = [self.error(503, "45"), self.response()]
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            fetch_response("https://example.com", "test", 25, retries=1, sleeper=waits.append)
        self.assertEqual(waits, [45])

    def test_http_date_and_invalid_retry_after_values(self):
        now = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds("Mon, 14 Sep 2026 00:00:45 GMT", now), 45)
        self.assertEqual(retry_after_seconds("Sun, 13 Sep 2026 00:00:00 GMT", now), 0)
        self.assertEqual(retry_after_seconds("not-a-date", now), 0)

    def test_long_server_cooldown_does_not_trigger_an_early_retry(self):
        waits = []
        opener = MagicMock()
        opener.open.side_effect = self.error(429, "900")
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            with self.assertRaises(RetryDeferred):
                fetch_response("https://example.com", "test", 25, retries=2, sleeper=waits.append)
        self.assertEqual(waits, [])
        self.assertEqual(opener.open.call_count, 1)

    def test_forbidden_is_not_retried(self):
        opener = MagicMock()
        opener.open.side_effect = self.error(403)
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            with self.assertRaises(HTTPError):
                fetch_response("https://example.com", "test", 25, retries=2)
        self.assertEqual(opener.open.call_count, 1)
