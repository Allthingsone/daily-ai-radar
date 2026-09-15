import unittest
from datetime import datetime, timezone

from daily_radar.models import RadarItem
from daily_radar.verification import arxiv_api_verification, domain_matches


class VerificationTests(unittest.TestCase):
    def test_domain_match_accepts_subdomains_but_not_lookalikes(self):
        self.assertTrue(domain_matches("https://blogs.nvidia.com/post", ["nvidia.com"]))
        self.assertFalse(domain_matches("https://nvidia.com.example.org/post", ["nvidia.com"]))

    def test_arxiv_identity_requires_id_and_canonical_url_to_match(self):
        item = RadarItem(
            kind="paper",
            title="Verified Paper",
            url="https://arxiv.org/abs/2607.29052",
            canonical_url="https://arxiv.org/abs/2607.29052",
            source_id="arxiv",
            source_name="arXiv",
            source_tier=1,
            published_at=datetime.now(timezone.utc),
            external_id="2607.29052",
        )
        result = arxiv_api_verification(item, "https://export.arxiv.org/api/query")
        self.assertEqual(result.status, "verified-primary")
        item.external_id = "2607.00000"
        result = arxiv_api_verification(item, "https://export.arxiv.org/api/query")
        self.assertEqual(result.status, "invalid-record")

    def test_listing_provenance_requires_current_new_submission_evidence(self):
        announced_at = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
        official_url = "https://arxiv.org/list/cs.AI/new"
        metadata = {
            "collection_method": "arxiv-new-list", "arxiv_id": "2609.12000",
            "announcement_listing_url": official_url, "announcement_listing_date": "2026-09-14",
            "announcement_batch_at": announced_at.isoformat(), "announcement_type": "new",
            "published_at_verified": True, "is_new_submission": True,
        }
        item = RadarItem(
            kind="paper", title="Verified new listing", url="https://arxiv.org/abs/2609.12000",
            canonical_url="https://arxiv.org/abs/2609.12000", source_id="arxiv", source_name="arXiv",
            source_tier=1, published_at=announced_at, external_id="2609.12000", metadata=metadata,
        )
        self.assertTrue(arxiv_api_verification(item, official_url).usable)
        for change in (
            {"announcement_listing_date": "2026-09-11"},
            {"announcement_batch_at": ""}, {"announcement_type": "cross"},
            {"is_new_submission": False}, {"published_at_verified": False},
            {"arxiv_id": "2609.12001"},
            {"announcement_listing_url": "https://arxiv.org.example.com/list/cs.AI/new"},
        ):
            item.metadata = {**metadata, **change}
            with self.subTest(change=change):
                self.assertFalse(arxiv_api_verification(item, official_url).usable)
        item.metadata = metadata
        for url in ("https://example.com/list/cs.AI/new", "https://arxiv.org/list/cs.AI/recent",
                    "https://export.arxiv.org/api/query"):
            with self.subTest(source=url):
                self.assertFalse(arxiv_api_verification(item, url).usable)

    def test_api_provenance_rejects_nonofficial_endpoints(self):
        item = RadarItem(
            kind="paper", title="Paper", url="https://arxiv.org/abs/2609.12000",
            canonical_url="https://arxiv.org/abs/2609.12000", source_id="arxiv", source_name="arXiv",
            source_tier=1, published_at=datetime.now(timezone.utc), external_id="2609.12000",
        )
        for url in ("https://export.arxiv.org.example.com/api/query", "https://arxiv.org/api/query",
                    "https://export.arxiv.org/not-an-api"):
            with self.subTest(source=url):
                self.assertFalse(arxiv_api_verification(item, url).usable)


if __name__ == "__main__":
    unittest.main()
