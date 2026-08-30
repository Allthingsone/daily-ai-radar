import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from daily_radar.config import load_settings
from daily_radar.db import Database
from daily_radar.mailer import send_daily_email
from daily_radar.sample import build_demo_items


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout, context):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message


class MailerTests(unittest.TestCase):
    def test_same_163_account_can_send_to_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "radar.db")
            database.initialize()
            now = datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc)
            news, paper = build_demo_items()[0], build_demo_items()[4]
            for item in (news, paper):
                item.metadata["demo"] = False
                item.metadata["provenance"] = {"status": "verified-primary"}
                item.published_at = now
                item.is_important = True
                database.upsert_item(item)

            settings = load_settings()
            settings = replace(
                settings,
                email=replace(
                    settings.email,
                    username="radar@example.com",
                    auth_code="smtp-auth-code",
                    recipient="radar@example.com",
                ),
            )
            FakeSMTP.instances.clear()
            result = send_daily_email(
                settings,
                database,
                site_url="https://example.github.io/daily-ai-radar/",
                now=now,
                smtp_factory=FakeSMTP,
            )

            smtp = FakeSMTP.instances[0]
            self.assertEqual(smtp.host, "smtp.163.com")
            self.assertEqual(smtp.login_args, ("radar@example.com", "smtp-auth-code"))
            self.assertEqual(smtp.message["To"], "radar@example.com")
            self.assertEqual(result["news"], 1)
            self.assertEqual(result["papers"], 1)
            self.assertNotIn("smtp-auth-code", smtp.message.as_string())


if __name__ == "__main__":
    unittest.main()
