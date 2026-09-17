import unittest
from datetime import datetime, timezone
from email.message import Message
from html import escape
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from daily_radar.collectors.arxiv import ArxivCollector
from daily_radar.collectors.arxiv_listing import ArxivListingCollector, parse_listing_page
from daily_radar.collectors.base import FetchResponse
from daily_radar.config import NetworkSettings, PaperSettings
from daily_radar.verification import arxiv_api_verification


ANNOUNCEMENT = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
AFTER_RELEASE = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)


def entry(identifier, primary="cs.AI", section="new", title="Driving & perception"):
    return {"id": identifier, "primary": primary, "section": section, "title": title}


def listing(entries, day="Monday, 14 September 2026", total=None, separate_sections=True):
    count = len(entries) if total is None else total
    fragments = [f"<h3>Showing new listings for {day}</h3><div class='paging'>Total of {count} entries</div>"]
    if not separate_sections:
        fragments.append("<dl id='articles'>")
    for section, name in (("new", "New submissions"), ("cross", "Cross submissions"), ("replace", "Replacement submissions")):
        selected = [value for value in entries if value["section"] == section]
        if not selected:
            continue
        if separate_sections:
            fragments.append("<dl id='articles'>")
        fragments.append(f"<h3>{name} (showing {len(selected)} of {len(selected)} entries)</h3>")
        for value in selected:
            identifier, primary = value["id"], value["primary"]
            fragments.append(f"""<dt><a href="/abs/{identifier}" title="Abstract">arXiv:{identifier}</a></dt>
              <dd><div class="meta">
                <div class="list-title mathjax"><span class="descriptor">Title:</span>{escape(value['title'])}</div>
                <div class="list-authors"><a href="/search/?author=A">A. Author</a>, <a href="/search/?author=B">B. Author</a></div>
                <div class="list-subjects"><span class="descriptor">Subjects:</span><span class="primary-subject">Primary ({primary})</span>; Artificial Intelligence (cs.AI)</div>
                <div class="list-comments mathjax"><span class="descriptor">Comments:</span>Code: https://github.com/example/drive</div>
                <p class="mathjax">Complete abstract with a <em>multimodal</em> driving evaluation.</p>
              </div></dd>""")
        if separate_sections:
            fragments.append("</dl>")
    if not separate_sections:
        fragments.append("</dl>")
    return "".join(fragments).encode()


def response(url, payload):
    return FetchResponse(payload, url, 200, "text/html")


class ArxivListingTests(unittest.TestCase):
    def test_parse_preserves_sections_abstracts_and_identity(self):
        page = parse_listing_page(listing([
            entry("2609.12000"), entry("2609.12001", "cs.RO", "cross"),
            entry("2501.00001", "cs.AI", "replace"),
        ]))
        self.assertEqual(page.listing_date.isoformat(), "2026-09-14")
        self.assertEqual(page.total_entries, 3)
        self.assertEqual([item.section for item in page.entries], ["new", "cross", "replace"])
        self.assertEqual(page.entries[0].title, "Driving & perception")
        self.assertEqual(page.entries[0].summary, "Complete abstract with a multimodal driving evaluation.")
        self.assertEqual(page.entries[0].authors, ["A. Author", "B. Author"])

    def test_sections_inside_one_list_with_legacy_headings_are_also_supported(self):
        payload = listing([
            entry("2609.12000"), entry("2609.12001", "cs.RO", "cross"),
            entry("2501.00001", "cs.AI", "replace"),
        ], separate_sections=False)
        payload = payload.replace(b"Cross submissions", b"Cross-lists").replace(b"Replacement submissions", b"Replacements")
        self.assertEqual([value.section for value in parse_listing_page(payload).entries], ["new", "cross", "replace"])

    def test_malformed_identity_or_missing_completeness_information_is_rejected(self):
        payload = listing([entry("2609.12000")])
        for damaged in (
            payload.replace(b'href="/abs/2609.12000"', b'href="https://evil.example/abs/2609.12000"'),
            payload.replace(b"arXiv:2609.12000", b"arXiv:2609.12001"),
            payload.replace(b"Showing new listings for", b"Missing listing date"),
            payload.replace(b"Total of 1 entries", b"Unknown count"),
            payload.replace(b'class="mathjax"', b'class="missing-abstract"'),
            payload.replace(b"showing 1 of 1", b"showing 2 of 2"),
            payload.replace(b"showing 1 of 1", b"showing 0 of 1"),
        ):
            with self.subTest(payload=damaged[:80]), self.assertRaises(ValueError):
                parse_listing_page(damaged)

    def test_all_categories_and_cross_list_primary_sources_are_verified(self):
        documents = {
            "cs.AI": listing([
                entry("2609.12000"), entry("2609.12001", "cs.CR", "cross"),
                entry("2501.00001", "cs.CR", "cross"), entry("2502.00001", "cs.AI", "replace"),
            ]),
            "cs.CV": listing([entry("2609.12002", "cs.CV")]),
            "cs.CR": listing([entry("2609.12001", "cs.CR")]),
        }
        calls, waits = [], []

        def fetch(url, **kwargs):
            category = urlsplit(url).path.split("/")[2]
            calls.append(category)
            return response(url, documents[category])

        collector = ArxivListingCollector(PaperSettings(categories=["cs.AI", "cs.CV"]),
                                          NetworkSettings(), "Asia/Shanghai", fetch, waits.append)
        result = collector.collect(ANNOUNCEMENT)
        self.assertEqual(calls, ["cs.AI", "cs.CV", "cs.CR"])
        self.assertEqual(waits, [3, 3, 3])
        self.assertEqual([item.external_id for item in result.items], ["2609.12000", "2609.12001", "2609.12002"])
        self.assertEqual(result.details["old_cross_lists_excluded"], 1)
        for item in result.items:
            self.assertEqual(item.published_at, ANNOUNCEMENT)
            self.assertTrue(item.metadata["is_new_submission"])
            self.assertNotIn("arxiv_first_submitted_at", item.metadata)
            provenance = arxiv_api_verification(item, result.final_url)
            self.assertTrue(provenance.usable)
            self.assertEqual(provenance.method, "arxiv-announcement-list")

    def test_cross_lists_already_seen_as_new_are_deduplicated(self):
        documents = {
            "cs.AI": listing([entry("2609.12000")]),
            "cs.CV": listing([entry("2609.12000", "cs.AI", "cross")]),
        }
        collector = ArxivListingCollector(
            PaperSettings(categories=list(documents)), NetworkSettings(), "Asia/Shanghai",
            lambda url, **kwargs: response(url, documents[urlsplit(url).path.split("/")[2]]), lambda _: None,
        )
        self.assertEqual(len(collector.collect(ANNOUNCEMENT).items), 1)

    def test_stale_or_redirected_category_does_not_publish_partial_results(self):
        for stale, redirect in ((True, False), (False, True)):
            def fetch(url, **kwargs):
                if "/cs.CV/" in url:
                    day = "Friday, 11 September 2026" if stale else "Monday, 14 September 2026"
                    return response("https://evil.example/list/cs.CV/new" if redirect else url,
                                    listing([entry("2609.12001", "cs.CV")], day=day))
                return response(url, listing([entry("2609.12000")]))
            collector = ArxivListingCollector(PaperSettings(categories=["cs.AI", "cs.CV"]),
                                              NetworkSettings(), "Asia/Shanghai", fetch, lambda _: None)
            with self.subTest(stale=stale, redirect=redirect), self.assertRaises(ValueError):
                collector.collect(ANNOUNCEMENT)

    def test_pagination_collects_every_advertised_entry(self):
        starts = []

        def fetch(url, **kwargs):
            start = int(parse_qs(urlsplit(url).query)["skip"][0])
            starts.append(start)
            return response(url, listing([entry(f"2609.{12000 + start}")], total=2))

        collector = ArxivListingCollector(PaperSettings(categories=["cs.AI"]),
                                          NetworkSettings(), "Asia/Shanghai", fetch, lambda _: None)
        self.assertEqual(len(collector.collect(ANNOUNCEMENT).items), 2)
        self.assertEqual(starts, [0, 1])

    def test_truncated_or_repeated_pages_fail(self):
        for repeat in (False, True):
            def fetch(url, **kwargs):
                start = int(parse_qs(urlsplit(url).query)["skip"][0])
                values = [entry("2609.12000")] if not start or repeat else []
                return response(url, listing(values, total=2))
            collector = ArxivListingCollector(PaperSettings(categories=["cs.AI"]),
                                              NetworkSettings(), "Asia/Shanghai", fetch, lambda _: None)
            with self.subTest(repeat=repeat), self.assertRaises(ValueError):
                collector.collect(ANNOUNCEMENT)

    def test_explicitly_empty_dated_category_is_a_valid_complete_result(self):
        collector = ArxivListingCollector(PaperSettings(categories=["cs.AI"]),
                                          NetworkSettings(), "Asia/Shanghai",
                                          lambda url, **kwargs: response(url, listing([])), lambda _: None)
        result = collector.collect(ANNOUNCEMENT)
        self.assertEqual(result.items, [])
        self.assertEqual(result.details["listing_pages"], 1)

    def test_reusing_the_collector_keeps_each_runs_provenance_separate(self):
        collector = ArxivListingCollector(PaperSettings(categories=["cs.AI"]),
                                          NetworkSettings(), "Asia/Shanghai",
                                          lambda url, **kwargs: response(url, listing([])), lambda _: None)
        first = collector.collect(ANNOUNCEMENT)
        second = collector.collect(ANNOUNCEMENT)
        self.assertEqual(first.details["listing_pages"], 1)
        self.assertEqual(second.details["listing_pages"], 1)
        self.assertEqual(len(first.details["listing_urls"]), 1)
        with self.assertRaises(ValueError):
            collector.collect(ANNOUNCEMENT.replace(tzinfo=None))

    def test_unavailable_or_malformed_api_uses_the_verified_listing(self):
        for failure in ("406", "408", "429", "500", "502", "503", "504", "timeout", "empty", "malformed"):
            waits = []

            def fetch(url, **kwargs):
                if "export.arxiv.org" in url:
                    if failure.isdigit():
                        raise HTTPError(url, int(failure), "API unavailable", Message(), BytesIO())
                    if failure == "timeout":
                        raise TimeoutError("The read operation timed out")
                    if failure == "malformed":
                        return FetchResponse(b"<incomplete", url, 200, "application/atom+xml")
                    return FetchResponse(b'<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>0</opensearch:totalResults></feed>', url, 200, "application/atom+xml")
                return response(url, listing([entry("2609.12000")]))

            collector = ArxivCollector(PaperSettings(categories=["cs.AI"]), NetworkSettings(), sleeper=waits.append)
            with self.subTest(failure=failure), patch("daily_radar.collectors.arxiv.fetch_response", fetch):
                result = collector.collect(AFTER_RELEASE)
            self.assertEqual(result.error, "")
            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.details["collection_method"], "arxiv-new-list")
            self.assertIn("api_error", result.details)
            if failure == "429":
                self.assertEqual(waits, [30, 3])
            elif failure.isdigit():
                self.assertEqual(waits, [3])

    def test_406_fallback_honors_both_cooldowns_and_recovers_from_listing_429(self):
        api_headers = Message()
        api_headers["Retry-After"] = "6"
        listing_headers = Message()
        listing_headers["Retry-After"] = "45"
        listing_url = "https://arxiv.org/list/cs.AI/new?skip=0&show=2000"
        success = MagicMock()
        success.__enter__.return_value = success
        success.read.return_value = listing([entry("2609.12000")])
        success.headers = {"Content-Type": "text/html"}
        success.geturl.return_value = listing_url
        success.status = 200
        opener = MagicMock()
        opener.open.side_effect = [
            HTTPError("https://export.arxiv.org/api/query", 406, "Not Acceptable", api_headers, BytesIO()),
            HTTPError(listing_url, 429, "Too Many Requests", listing_headers, BytesIO()),
            success,
        ]
        waits = []
        collector = ArxivCollector(PaperSettings(categories=["cs.AI"]), NetworkSettings(), sleeper=waits.append)
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            result = collector.collect(AFTER_RELEASE)
        self.assertEqual(result.error, "")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(waits, [6, 3, 45])
        self.assertEqual(opener.open.call_count, 3)
        requested_urls = [call.args[0].full_url for call in opener.open.call_args_list]
        self.assertEqual(sum("export.arxiv.org" in url for url in requested_urls), 1)
        self.assertEqual(requested_urls[1:], [listing_url, listing_url])
        self.assertIn("HTTP Error 406", result.details["api_error"])
        self.assertEqual(result.details["collection_method"], "arxiv-new-list")

    def test_406_fallback_never_publishes_when_listing_rate_limit_persists(self):
        opener = MagicMock()
        opener.open.side_effect = [
            HTTPError("https://export.arxiv.org/api/query", 406, "Not Acceptable", Message(), BytesIO()),
            *[HTTPError("https://arxiv.org/list/cs.AI/new", 429, "Too Many Requests", Message(), BytesIO())
              for _ in range(3)],
        ]
        waits = []
        collector = ArxivCollector(PaperSettings(categories=["cs.AI"]), NetworkSettings(), sleeper=waits.append)
        with patch("daily_radar.collectors.base.build_http_opener", return_value=opener):
            result = collector.collect(AFTER_RELEASE)
        self.assertEqual(result.items, [])
        self.assertIn("HTTP Error 406", result.error)
        self.assertIn("HTTP Error 429", result.error)
        self.assertIn("HTTP Error 429", result.details["fallback_error"])
        self.assertEqual(waits, [3, 30, 60])
        self.assertEqual(opener.open.call_count, 4)

    def test_failed_backup_preserves_both_errors_without_partial_items(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            if "export.arxiv.org" in url:
                raise HTTPError(url, 429, "Too Many Requests", Message(), BytesIO())
            if "/cs.CV/" in url:
                raise TimeoutError("official category unavailable")
            return response(url, listing([entry("2609.12000")]))

        collector = ArxivCollector(PaperSettings(categories=["cs.AI", "cs.CV"]),
                                   NetworkSettings(), sleeper=lambda _: None)
        with patch("daily_radar.collectors.arxiv.fetch_response", fetch):
            result = collector.collect(AFTER_RELEASE)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.items, [])
        self.assertEqual(result.http_status, 429)
        self.assertFalse(result.domain_match)
        self.assertIn("HTTP Error 429", result.details["api_error"])
        self.assertIn("official category unavailable", result.details["fallback_error"])
        self.assertIn("official listing fallback failed", result.error)

    def test_long_api_cooldown_or_forbidden_does_not_try_another_endpoint(self):
        for code, cooldown in ((406, "900"), (429, "900"), (400, ""), (401, ""), (403, "")):
            headers = Message()
            headers["Retry-After"] = cooldown
            collector = ArxivCollector(PaperSettings(categories=["cs.AI"]), NetworkSettings(), sleeper=lambda _: None)
            with patch("daily_radar.collectors.arxiv.fetch_response",
                       side_effect=HTTPError("https://export.arxiv.org/api/query", code, "busy", headers, BytesIO())) as fetch:
                result = collector.collect(AFTER_RELEASE)
            self.assertTrue(result.error)
            self.assertEqual(result.items, [])
            self.assertEqual(fetch.call_count, 1)
