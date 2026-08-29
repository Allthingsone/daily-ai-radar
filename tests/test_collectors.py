import unittest

from daily_radar.collectors.arxiv import ArxivCollector
from daily_radar.collectors.rss import parse_feed
from daily_radar.config import NetworkSettings, PaperSettings, SourceConfig


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title><item>
  <title>New AI Model</title>
  <link>https://example.com/model?utm_source=feed</link>
  <guid>story-1</guid>
  <pubDate>Mon, 03 Aug 2026 02:00:00 GMT</pubDate>
  <description><![CDATA[<p>A multimodal model release.</p>]]></description>
</item></channel></rss>"""

ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.01234v2</id>
    <updated>2026-08-03T01:00:00Z</updated><published>2026-08-03T01:00:00Z</published>
    <title>DriveVLA for Autonomous Driving</title>
    <summary>A vision-language-action driving agent.</summary>
    <author><name>A. Researcher</name></author>
    <category term="cs.CV"/>
    <link title="pdf" href="https://arxiv.org/pdf/2608.01234v2" type="application/pdf"/>
    <arxiv:comment>Code: https://github.com/example/drivevla</arxiv:comment>
  </entry>
</feed>"""


class CollectorParserTests(unittest.TestCase):
    def test_rss_parser(self):
        source = SourceConfig(
            id="test", name="Test", url="https://example.com/feed", tier=1
        )
        items = parse_feed(RSS, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].canonical_url, "https://example.com/model")
        self.assertEqual(items[0].summary, "A multimodal model release.")

    def test_arxiv_parser(self):
        items = ArxivCollector.parse(ARXIV)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].external_id, "2608.01234")
        self.assertEqual(items[0].metadata["code_url"], "https://github.com/example/drivevla")
        self.assertEqual(items[0].authors, ["A. Researcher"])

    def test_arxiv_query_is_domain_targeted(self):
        collector = ArxivCollector(PaperSettings(), NetworkSettings())
        url = collector.build_url()
        self.assertIn("autonomous+driving", url)
        self.assertIn("vision-language", url)
        self.assertIn("cat%3Acs.CV", url)


if __name__ == "__main__":
    unittest.main()
