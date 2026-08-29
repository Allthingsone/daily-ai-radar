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


if __name__ == "__main__":
    unittest.main()
