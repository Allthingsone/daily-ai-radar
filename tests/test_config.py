import os
import unittest
from unittest.mock import patch

from daily_radar.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_user_agent_can_be_supplied_by_cloud_environment(self):
        expected = "DailyAIRadar/test (contact: radar@example.com)"
        with patch.dict(os.environ, {"DAILY_RADAR_USER_AGENT": expected}):
            self.assertEqual(load_settings().network.user_agent, expected)


if __name__ == "__main__":
    unittest.main()
