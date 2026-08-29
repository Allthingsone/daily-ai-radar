import unittest

from daily_radar.processing.normalize import (
    canonicalize_url,
    fingerprint_title,
    title_similarity,
)


class NormalizeTests(unittest.TestCase):
    def test_tracking_parameters_and_fragments_are_removed(self):
        value = "https://www.Example.com/story/?utm_source=x&b=2&a=1#section"
        self.assertEqual(canonicalize_url(value), "https://example.com/story?a=1&b=2")

    def test_arxiv_versions_and_pdf_urls_share_one_canonical_url(self):
        self.assertEqual(
            canonicalize_url("https://arxiv.org/pdf/2608.01234v2.pdf?download=1"),
            "https://arxiv.org/abs/2608.01234",
        )

    def test_title_similarity_clusters_reworded_headline(self):
        left = "OpenAI releases a new multimodal reasoning model"
        right = "New multimodal reasoning model released by OpenAI"
        self.assertGreater(title_similarity(left, right), 0.72)

    def test_fingerprint_is_stable(self):
        self.assertEqual(
            fingerprint_title("The New AI Model!"),
            fingerprint_title("new AI model"),
        )


if __name__ == "__main__":
    unittest.main()

