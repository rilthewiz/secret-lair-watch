"""Discord webhook notifier tests. No network: urlopen is replaced by a spy."""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from lairwatch.config import DiscordConfig, EmailConfig, NotifierConfig
from lairwatch.notifiers import Notification, NotificationBatch, build_notifiers
from lairwatch.notifiers.base import NotifierError
from lairwatch.notifiers.discord import DiscordNotifier, split_message

WEBHOOK = "https://discord.com/api/webhooks/123456789/abcdef-token"


def batch(text: str = "Doomsdrop is coming soon") -> NotificationBatch:
    return NotificationBatch(
        [
            Notification(
                source="secret_lair_store",
                kind="new",
                title=text,
                url="https://secretlair.wizards.com/us",
                status="Coming Soon",
            )
        ]
    )


class FakeResponse:
    status = 204

    def __init__(self) -> None:
        self.headers = {}

    def read(self) -> bytes:
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def http_error(code: int, body: bytes = b"", headers: dict | None = None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(WEBHOOK, code, "error", message, io.BytesIO(body))


class SplitTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self) -> None:
        self.assertEqual(split_message("hello"), ["hello"])

    def test_chunks_respect_the_limit(self) -> None:
        text = "\n".join(f"line {index}" for index in range(500))
        chunks = split_message(text, limit=200)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 200)

    def test_long_single_line_is_hard_split(self) -> None:
        chunks = split_message("x" * 450, limit=200)
        self.assertEqual([len(chunk) for chunk in chunks], [200, 200, 50])

    def test_blank_text_produces_nothing(self) -> None:
        self.assertEqual(split_message("   \n\n"), [])


class ValidationTests(unittest.TestCase):
    def test_rejects_plain_http(self) -> None:
        with self.assertRaises(NotifierError):
            DiscordNotifier("http://discord.com/api/webhooks/1/x").send(batch())

    def test_rejects_non_discord_host(self) -> None:
        with self.assertRaises(NotifierError) as raised:
            DiscordNotifier("https://evil.example/api/webhooks/1/x").send(batch())
        self.assertIn("not a Discord host", str(raised.exception))

    def test_rejects_non_webhook_path(self) -> None:
        with self.assertRaises(NotifierError):
            DiscordNotifier("https://discord.com/channels/1/2").send(batch())

    def test_rejects_empty_url(self) -> None:
        with self.assertRaises(NotifierError):
            DiscordNotifier("").send(batch())


class SendTests(unittest.TestCase):
    def test_posts_json_and_suppresses_mentions(self) -> None:
        captured = []

        def fake_urlopen(request, timeout=None):
            captured.append(request)
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            DiscordNotifier(WEBHOOK).send(batch("Doomsdrop @everyone"))

        self.assertEqual(len(captured), 1)
        payload = json.loads(captured[0].data.decode("utf-8"))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("Doomsdrop @everyone", payload["content"])
        self.assertEqual(payload["username"], "Secret Lair Watch")
        self.assertEqual(captured[0].get_method(), "POST")

    def test_long_digest_is_split_into_several_posts(self) -> None:
        captured = []

        def fake_urlopen(request, timeout=None):
            captured.append(request)
            return FakeResponse()

        many = NotificationBatch(
            [
                Notification(
                    source="s",
                    kind="new",
                    title=f"Drop {index} " + "x" * 100,
                    url="https://secretlair.wizards.com/us",
                    status="Coming Soon",
                )
                for index in range(40)
            ]
        )
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            DiscordNotifier(WEBHOOK).send(many)

        self.assertGreater(len(captured), 1)
        for request in captured:
            content = json.loads(request.data.decode("utf-8"))["content"]
            self.assertLessEqual(len(content), 1800)

    def test_rate_limit_is_retried_once_honouring_hint(self) -> None:
        calls = []
        body = json.dumps({"retry_after": 0}).encode()

        def fake_urlopen(request, timeout=None):
            calls.append(request)
            if len(calls) == 1:
                raise http_error(429, body)
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            DiscordNotifier(WEBHOOK, rate_limit_wait=0).send(batch())

        self.assertEqual(len(calls), 2)

    def test_long_rate_limit_gives_up_instead_of_hanging(self) -> None:
        body = json.dumps({"retry_after": 3600}).encode()

        def fake_urlopen(request, timeout=None):
            raise http_error(429, body)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(NotifierError):
                DiscordNotifier(WEBHOOK).send(batch())

    def test_http_error_is_wrapped(self) -> None:
        def fake_urlopen(request, timeout=None):
            raise http_error(400, b'{"message": "bad"}')

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(NotifierError) as raised:
                DiscordNotifier(WEBHOOK).send(batch())
        self.assertIn("HTTP 400", str(raised.exception))

    def test_unreachable_host_is_wrapped(self) -> None:
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError("dns failure")

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(NotifierError) as raised:
                DiscordNotifier(WEBHOOK).send(batch())
        self.assertIn("unreachable", str(raised.exception))

    def test_error_message_never_contains_the_webhook(self) -> None:
        def fake_urlopen(request, timeout=None):
            raise http_error(403)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            try:
                DiscordNotifier(WEBHOOK).send(batch())
            except NotifierError as exc:
                self.assertNotIn("abcdef-token", str(exc))
            else:  # pragma: no cover
                self.fail("expected NotifierError")


class FactoryTests(unittest.TestCase):
    def _config(self, enabled: bool = True) -> NotifierConfig:
        return NotifierConfig(
            console_enabled=True,
            desktop_enabled=False,
            discord=DiscordConfig(enabled=enabled, webhook_env="LAIRWATCH_TEST_HOOK"),
            email=EmailConfig(enabled=False),
        )

    def test_disabled_discord_is_not_built(self) -> None:
        with mock.patch.dict(os.environ, {"LAIRWATCH_TEST_HOOK": WEBHOOK}, clear=False):
            names = [notifier.name for notifier in build_notifiers(self._config(False))]
        self.assertEqual(names, ["console"])

    def test_enabled_discord_is_built_from_the_environment(self) -> None:
        with mock.patch.dict(os.environ, {"LAIRWATCH_TEST_HOOK": WEBHOOK}, clear=False):
            names = [notifier.name for notifier in build_notifiers(self._config())]
        self.assertEqual(names, ["console", "discord"])

    def test_missing_webhook_env_skips_instead_of_crashing(self) -> None:
        environ = {k: v for k, v in os.environ.items() if k != "LAIRWATCH_TEST_HOOK"}
        with mock.patch.dict(os.environ, environ, clear=True):
            names = [notifier.name for notifier in build_notifiers(self._config())]
        self.assertEqual(names, ["console"])

    def test_bad_webhook_url_is_skipped(self) -> None:
        with mock.patch.dict(
            os.environ, {"LAIRWATCH_TEST_HOOK": "https://evil.example/api/webhooks/1"}
        ):
            names = [notifier.name for notifier in build_notifiers(self._config())]
        self.assertEqual(names, ["console"])

    def test_dry_run_never_builds_discord(self) -> None:
        with mock.patch.dict(os.environ, {"LAIRWATCH_TEST_HOOK": WEBHOOK}, clear=False):
            names = [
                notifier.name for notifier in build_notifiers(self._config(), dry_run=True)
            ]
        self.assertEqual(names, ["console"])


if __name__ == "__main__":
    unittest.main()
