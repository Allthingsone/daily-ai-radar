import unittest
from datetime import datetime, timezone
from html import escape
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from daily_radar.collectors.arxiv import ArxivCollector
from daily_radar.collectors.arxiv_recent import ArxivRecentCollector, parse_recent_page
from daily_radar.collectors.base import FetchResponse
from daily_radar.config import NetworkSettings, PaperSettings
from daily_radar.verification import arxiv_api_verification


ANNOUNCED = datetime(2026, 11, 17, 1, tzinfo=timezone.utc)


def record(identifier, primary="cs.RO", section="new"):
    return {"id": identifier, "primary": primary, "section": section}


def recent(groups, total=None):
    # groups: (date heading, records on this page, full day's entry count)
    total = sum(group[2] for group in groups) if total is None else total
    out = [f"<div class='paging'>Total of {total} entries</div>"]
    for day, records, count in groups:
        out.append(f"<dl id='articles'><h3>{day} (showing {len(records)} of {count} entries )</h3>")
        for item in records:
            identifier, primary, section = item["id"], item["primary"], item["section"]
            marker = f"(cross-list from {primary})" if section == "cross" else "(replaced)" if section == "replace" else ""
            out.append(f'''<dt><a title="Abstract" href="/abs/{identifier}">arXiv:{identifier}</a>{marker}
              <a href="https://arxiv.org/html/{identifier}v2">html</a></dt>
              <dd><div class="list-title">Title: Paper {identifier}</div>
              <div class="list-authors"><a>Example Author</a></div>
              <div class="list-subjects"><span class="primary-subject">Subject ({primary})</span></div></dd>''')
        out.append("</dl>")
    return "".join(out).encode()


def metadata(ids, *, version=1, first_submitted="2026-11-10T10:00:00Z"):
    entries = "".join(f'''<entry><id>http://arxiv.org/abs/{escape(identifier)}v{version}</id>
      <title>Paper {identifier}</title><summary>Full original abstract for semantic screening.</summary>
      <published>{first_submitted}</published><updated>{first_submitted}</updated>
      <author><name>Example Author</name></author><category term="cs.RO"/></entry>''' for identifier in ids)
    return f'''<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>{len(ids)}</opensearch:totalResults>{entries}</feed>'''.encode()


class RecentAnnouncementTests(unittest.TestCase):
    def collector(self, fetcher, categories=None, page_size=200):
        return ArxivRecentCollector(
            PaperSettings(categories=categories or ["cs.RO"], page_size=page_size),
            NetworkSettings(), "Asia/Shanghai", fetcher, lambda _: None, ArxivCollector.parse_page,
        )

    def test_historical_membership_and_v1_metadata_include_delayed_submissions(self):
        calls = []
        document = recent([
            ("Wed, 18 Nov 2026", [record("2611.23000")], 1),
            ("Tue, 17 Nov 2026", [record("2611.21000"), record("2611.21001")], 2),
            ("Mon, 16 Nov 2026", [record("2611.19000")], 1),
        ])
        def fetch(url, **kwargs):
            calls.append(url)
            if "/recent?" in url:
                self.assertEqual(kwargs["timeout"], NetworkSettings().timeout_seconds)
                return FetchResponse(document, url, 200, "text/html")
            self.assertGreaterEqual(kwargs["timeout"], 60)
            ids = parse_qs(urlsplit(url).query)["id_list"][0].split(",")
            self.assertTrue(all(identifier.endswith("v1") for identifier in ids))
            self.assertNotIn("submittedDate", url)
            return FetchResponse(metadata([identifier[:-2] for identifier in ids]), url, 200, "application/atom+xml")
        result = self.collector(fetch, page_size=1).collect(ANNOUNCED)
        self.assertEqual([item.external_id for item in result.items], ["2611.21000", "2611.21001"])
        self.assertEqual(len(calls), 3)
        for item in result.items:
            self.assertEqual(item.published_at, ANNOUNCED)
            self.assertEqual(item.metadata["arxiv_first_submitted_at"], "2026-11-10T10:00:00+00:00")
            self.assertTrue(arxiv_api_verification(item, result.final_url).usable)
            item.metadata["announcement_listing_date"] = "2026-11-18"
            self.assertFalse(arxiv_api_verification(item, result.final_url).usable)

    def test_new_cross_lists_require_same_date_primary_listing_and_revisions_are_excluded(self):
        documents = {
            "cs.RO": recent([("Tue, 17 Nov 2026", [record("2611.21000"),
                record("2611.21001", "cs.CV", "cross"), record("2501.00001", "cs.CV", "cross"),
                record("2501.00002", section="replace")], 4)]),
            "cs.CV": recent([("Tue, 17 Nov 2026", [record("2611.21001", "cs.CV")], 1)]),
        }
        def fetch(url, **kwargs):
            if "/recent?" in url:
                data = documents[urlsplit(url).path.split("/")[2]]
            else:
                ids = parse_qs(urlsplit(url).query)["id_list"][0].split(",")
                data = metadata([identifier[:-2] for identifier in ids])
            return FetchResponse(data, url, 200, "text/html")
        result = self.collector(fetch).collect(ANNOUNCED)
        self.assertEqual([item.external_id for item in result.items], ["2611.21000", "2611.21001"])
        self.assertEqual(result.details["old_cross_lists_excluded"], 1)

    def test_target_day_can_span_pages_and_another_day(self):
        documents = [
            recent([("Wed, 18 Nov 2026", [record("2611.23000")], 1),
                    ("Tue, 17 Nov 2026", [record("2611.21000")], 2)], total=4),
            recent([("Tue, 17 Nov 2026", [record("2611.21001")], 2),
                    ("Mon, 16 Nov 2026", [record("2611.19000")], 1)], total=4),
        ]
        offsets = []
        def fetch(url, **kwargs):
            offset = int(parse_qs(urlsplit(url).query)["skip"][0])
            offsets.append(offset)
            return FetchResponse(documents[offset // 2], url, 200, "text/html")
        entries = self.collector(fetch)._category("cs.RO", ANNOUNCED.date())
        self.assertEqual(offsets, [0, 2])
        self.assertEqual([entry.identifier for entry in entries], ["2611.21000", "2611.21001"])

    def test_absent_date_is_empty_only_when_official_dates_bracket_it(self):
        document = recent([("Wed, 18 Nov 2026", [record("2611.23000")], 1),
                           ("Mon, 16 Nov 2026", [record("2611.19000")], 1)])
        calls = []
        def fetch(url, **kwargs):
            calls.append(url)
            return FetchResponse(document, url, 200, "text/html")
        result = self.collector(fetch).collect(ANNOUNCED)
        self.assertEqual(result.items, [])
        self.assertEqual(result.details["empty_reason"], "no_new_submissions")
        self.assertEqual(len(calls), 1)
        for day in ("Mon, 16 Nov 2026", "Wed, 18 Nov 2026"):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "complete"):
                self.collector(lambda url, **kw: FetchResponse(
                    recent([(day, [record("2611.21000")], 1)]), url, 200, "text/html",
                )).collect(ANNOUNCED)

    def test_missing_pages_count_changes_and_duplicate_ids_fail_closed(self):
        first = recent([("Tue, 17 Nov 2026", [record("2611.21000")], 2)], total=2)
        invalid_seconds = [first, recent([("Tue, 17 Nov 2026", [record("2611.21001")], 3)], total=3),
                           recent([("Mon, 16 Nov 2026", [record("2611.19000")], 1)], total=2)]
        for second in invalid_seconds:
            def fetch(url, **kwargs):
                offset = int(parse_qs(urlsplit(url).query)["skip"][0])
                return FetchResponse(second if offset else first, url, 200, "text/html")
            with self.subTest(second=second[:80]), self.assertRaises(ValueError):
                self.collector(fetch).collect(ANNOUNCED)

    def test_incomplete_or_noninitial_metadata_is_not_an_empty_success(self):
        for invalid in (metadata([]), metadata(["2611.21000"], version=2),
                        metadata(["2611.21001"]), metadata(["2611.21000"], first_submitted="2026-11-18T00:00:00Z")):
            def fetch(url, **kwargs):
                data = recent([("Tue, 17 Nov 2026", [record("2611.21000")], 1)]) if "/recent?" in url else invalid
                return FetchResponse(data, url, 200, "text/html")
            collector = ArxivCollector(PaperSettings(categories=["cs.RO"]), NetworkSettings(), sleeper=lambda _: None)
            with self.subTest(payload=invalid[-80:]), patch("daily_radar.collectors.arxiv.fetch_response", fetch):
                result = collector.collect(ANNOUNCED)
            self.assertTrue(result.error)
            self.assertEqual(result.items, [])
            self.assertEqual(result.details["announcement_status"], "unavailable")

    def test_page_identity_and_counts_must_be_complete(self):
        valid = recent([("Tue, 17 Nov 2026", [record("2611.21000")], 1)])
        for invalid in (valid.replace(b'/abs/2611.21000', b'https://evil.test/abs/2611.21000'),
                        valid.replace(b'showing 1 of 1', b'showing 2 of 2'),
                        valid.replace(b'Total of 1 entries', b'Unknown count'),
                        valid.replace(b'primary-subject', b'unknown')):
            with self.subTest(payload=invalid[:80]), self.assertRaises(ValueError):
                parse_recent_page(invalid)

    def test_official_no_updates_section_is_empty_but_bare_date_is_not_proof(self):
        empty = b"<div class='paging'>Total of 0 entries</div><dl id='articles'><h3>Tue, 17 Nov 2026</h3><p>No updates for this time period.</p></dl>"
        result = self.collector(lambda url, **kw: FetchResponse(empty, url, 200, "text/html")).collect(ANNOUNCED)
        self.assertEqual(result.items, [])
        self.assertEqual(result.details["empty_reason"], "no_new_submissions")
        with self.assertRaises(ValueError):
            parse_recent_page(empty.replace(b"No updates for this time period.", b"Please try again later."))


if __name__ == "__main__":
    unittest.main()
