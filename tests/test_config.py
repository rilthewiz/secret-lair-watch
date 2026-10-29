"""Configuration validation and environment override tests."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from lairwatch.config import ConfigError, load_config

from helpers import temp_dir

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.json"


class ConfigTests(unittest.TestCase):
    def test_defaults_when_no_file_exists(self) -> None:
        clean = {k: v for k, v in os.environ.items() if not k.startswith("LAIRWATCH_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            config = load_config(None)
        self.assertEqual(config.interval_seconds, 3600)
        self.assertEqual(len(config.sources), 3)
        self.assertFalse(config.notifiers.email.enabled)
        self.assertFalse(config.notifiers.discord.enabled)
        self.assertTrue(config.notifiers.console_enabled)
        self.assertEqual(config.notifiers.discord.webhook_env, "LAIRWATCH_DISCORD_WEBHOOK")
        self.assertEqual(config.drop_schedule, [])

    def test_sample_config_is_valid_and_has_placeholders_only(self) -> None:
        config = load_config(EXAMPLE)
        self.assertEqual(len(config.sources), 3)
        self.assertEqual(len(config.drop_schedule), 1)
        self.assertEqual(config.notifiers.email.smtp_host, "smtp.example.com")
        self.assertIn("example.com", config.notifiers.email.from_addr)
        # No credentials may live in the file.
        raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        email = raw["notifiers"]["email"]
        self.assertNotIn("password", email)
        self.assertNotIn("username", email)
        self.assertEqual(email["password_env"], "LAIRWATCH_SMTP_PASSWORD")

    def test_interval_below_floor_is_rejected(self) -> None:
        path = Path(temp_dir()) / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "interval_seconds": 60,
                    "sources": [
                        {
                            "name": "s",
                            "type": "wotc_sitemap",
                            "url": "https://magic.wizards.com/en/sitemap.xml",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_browser_user_agent_is_rejected(self) -> None:
        path = Path(temp_dir()) / "ua.json"
        path.write_text(
            json.dumps({"user_agent": "Mozilla/5.0 (compatible)"}), encoding="utf-8"
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_unknown_source_type_is_rejected(self) -> None:
        path = Path(temp_dir()) / "type.json"
        path.write_text(
            json.dumps(
                {
                    "sources": [
                        {"name": "x", "type": "scraper9000", "url": "https://example.com"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_duplicate_source_names_rejected(self) -> None:
        path = Path(temp_dir()) / "dup.json"
        source = {
            "name": "same",
            "type": "wotc_sitemap",
            "url": "https://magic.wizards.com/en/sitemap.xml",
        }
        path.write_text(json.dumps({"sources": [source, source]}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_email_enabled_requires_destination_fields(self) -> None:
        path = Path(temp_dir()) / "email.json"
        path.write_text(
            json.dumps({"notifiers": {"email": {"enabled": True}}}), encoding="utf-8"
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_discord_enabled_requires_a_named_env_var(self) -> None:
        path = Path(temp_dir()) / "discord.json"
        path.write_text(
            json.dumps(
                {"notifiers": {"discord": {"enabled": True, "webhook_env": "  "}}}
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_discord_webhook_is_never_read_from_the_config_file(self) -> None:
        path = Path(temp_dir()) / "leak.json"
        path.write_text(
            json.dumps(
                {
                    "notifiers": {
                        "discord": {
                            "enabled": True,
                            "webhook_url": "https://discord.com/api/webhooks/1/secret",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        config = load_config(path)
        self.assertEqual(config.notifiers.discord.webhook_env, "LAIRWATCH_DISCORD_WEBHOOK")
        self.assertFalse(hasattr(config.notifiers.discord, "webhook_url"))

    def test_naive_drop_time_is_rejected(self) -> None:
        path = Path(temp_dir()) / "sched.json"
        path.write_text(
            json.dumps(
                {
                    "drop_schedule": [
                        {
                            "label": "X",
                            "starts_at": "2026-11-02T09:00:00",
                            "url": "https://secretlair.wizards.com/us",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_environment_overrides(self) -> None:
        env = {
            "LAIRWATCH_INTERVAL_SECONDS": "1800",
            "LAIRWATCH_LOG_LEVEL": "debug",
            "LAIRWATCH_STATE_PATH": "custom/state.json",
            "LAIRWATCH_EMAIL_ENABLED": "false",
            "LAIRWATCH_DISCORD_ENABLED": "true",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = load_config(None)
        self.assertEqual(config.interval_seconds, 1800)
        self.assertEqual(config.log_level, "DEBUG")
        self.assertTrue(config.notifiers.discord.enabled)
        self.assertEqual(str(config.state_path), os.path.join("custom", "state.json"))

    def test_bad_environment_value_is_reported(self) -> None:
        with mock.patch.dict(
            os.environ, {"LAIRWATCH_INTERVAL_SECONDS": "soon"}, clear=False
        ):
            with self.assertRaises(ConfigError):
                load_config(None)

    def test_missing_config_file_is_reported(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(Path(temp_dir()) / "nope.json")

    def test_invalid_json_is_reported(self) -> None:
        path = Path(temp_dir()) / "broken.json"
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_utf8_bom_is_tolerated(self) -> None:
        # Notepad and `Set-Content -Encoding utf8` in Windows PowerShell both
        # write a BOM; a config edited that way must still load.
        path = Path(temp_dir()) / "bom.json"
        body = json.dumps({"interval_seconds": 1800})
        path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        config = load_config(path)
        self.assertEqual(config.interval_seconds, 1800)


if __name__ == "__main__":
    unittest.main()
