import os
import unittest
from unittest.mock import patch

from daily_radar.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_user_agent_can_be_supplied_by_cloud_environment(self):
        expected = "DailyAIRadar/test (contact: radar@example.com)"
        with patch.dict(os.environ, {"DAILY_RADAR_USER_AGENT": expected}):
            self.assertEqual(load_settings().network.user_agent, expected)

    def test_deepseek_secret_and_best_mode_are_loaded(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            settings = load_settings()
        self.assertEqual(settings.llm.api_key, "test-key")
        self.assertEqual(settings.llm.model, "deepseek-v4-pro")
        self.assertTrue(settings.llm.thinking_enabled)
        self.assertEqual(settings.llm.reasoning_effort, "max")
        self.assertEqual(settings.llm.max_output_tokens, 32768)
        self.assertEqual(settings.llm.news_batch_size, 8)
        self.assertEqual(settings.llm.paper_batch_size, 6)
        self.assertTrue(settings.llm.news_prompt_path.is_file())


if __name__ == "__main__":
    unittest.main()
