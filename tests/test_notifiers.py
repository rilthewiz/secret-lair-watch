"""Notifier formatting and failure-isolation tests (no SMTP connection)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from lairwatch.config import EmailConfig
from lairwatch.notifiers import Notification, NotificationBatch, build_notifiers, dispatch
from lairwatch.notifiers.base import NotifierError
from lairwatch.notifiers.email_notifier import EmailNotifier
from lairwatch.notifiers.desktop import DesktopNotifier


def batch() -> NotificationBatch:
    return NotificationBatch(
        [
            Notification(
                source="secret_lair_store",
                kind="new",
                title="Doomsdrop",
                url="https://secretlair.wizards.com/us#doomsdrop",
                status="Coming Soon",
            ),
            Notification(
                source="secret_lair_store",
                kind="status_change",
                title="Mood Swings",
                url="https://secretlair.wizards.com/us",
                status="On Sale Now",
                previous_status="Coming Soon",
            ),
        ]
    )


class FormattingTests(unittest.TestCase):
    def test_subject_counts_updates(self) -> None:
        self.assertEqual(batch().subject(), "Secret Lair Watch: 2 updates")

    def test_singular_subject(self) -> None:
        single = NotificationBatch(
            [Notification(source="s", kind="new", title="t", url="u", status="Coming Soon")]
        )
        self.assertEqual(single.subject(), "Secret Lair Watch: 1 update")

    def test_text_contains_both_kinds_and_manual_purchase_note(self) -> None:
        text = batch().as_text()
        self.assertIn("[NEW] Doomsdrop", text)
        self.assertIn("[STATUS CHANGE] Mood Swings", text)
        self.assertIn("previous status: Coming Soon", text)
        self.assertIn("complete", text.lower())
        self.assertIn("https://secretlair.wizards.com/us", text)

    def test_no_secrets_in_rendered_text(self) -> None:
        text = batch().as_text()
        self.assertNotIn("password", text.lower())
        self.assertNotIn("token", text.lower())


class DispatchTests(unittest.TestCase):
    def test_empty_batch_sends_nothing(self) -> None:
        calls = []

        class Notifier:
            name = "spy"

            def send(self, _batch):
                calls.append(_batch)

        self.assertEqual(dispatch([Notifier()], NotificationBatch([])), [])
        self.assertEqual(calls, [])

    def test_build_notifiers_returns_console_only_in_dry_run(self) -> None:
        from lairwatch.config import NotifierConfig

        config = NotifierConfig(
            console_enabled=True,
            desktop_enabled=True,
            email=EmailConfig(
                enabled=True,
                smtp_host="smtp.example.com",
                from_addr="you@example.com",
                to_addrs=["you@example.com"],
            ),
        )
        names = [notifier.name for notifier in build_notifiers(config, dry_run=True)]
        self.assertEqual(names, ["console"])


class EmailTests(unittest.TestCase):
    def test_missing_password_is_reported_without_leaking(self) -> None:
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            from_addr="you@example.com",
            to_addrs=["you@example.com"],
            username_env="LAIRWATCH_TEST_USER",
            password_env="LAIRWATCH_TEST_PASSWORD",
        )
        with mock.patch.dict(
            os.environ, {"LAIRWATCH_TEST_USER": "someone"}, clear=False
        ):
            os.environ.pop("LAIRWATCH_TEST_PASSWORD", None)
            with self.assertRaises(NotifierError) as raised:
                EmailNotifier(config).send(batch())
        message = str(raised.exception)
        self.assertIn("LAIRWATCH_TEST_PASSWORD", message)
        self.assertNotIn("someone", message)

    def test_smtp_failure_is_wrapped(self) -> None:
        config = EmailConfig(
            enabled=True,
            smtp_host="smtp.invalid",
            smtp_port=2525,
            from_addr="you@example.com",
            to_addrs=["you@example.com"],
        )

        class BrokenSMTP:
            def __init__(self, *args, **kwargs):
                raise OSError("connection refused")

        with mock.patch("smtplib.SMTP", BrokenSMTP):
            with self.assertRaises(NotifierError) as raised:
                EmailNotifier(config).send(batch())
        self.assertIn("SMTP delivery failed", str(raised.exception))


class DesktopTests(unittest.TestCase):
    def test_unsupported_system_is_not_enabled(self) -> None:
        self.assertFalse(DesktopNotifier(system="plan9").enabled())

    def test_windows_command_uses_env_vars_for_text(self) -> None:
        notifier = DesktopNotifier(system="windows")
        command = notifier._command("title", "body")
        self.assertEqual(command[0], "powershell")
        script = command[-1]
        self.assertIn("LAIRWATCH_TOAST_TITLE", script)
        self.assertIn("LAIRWATCH_TOAST_BODY", script)

    def test_windows_toast_calls_show_on_the_notifier_instance(self) -> None:
        # ToastNotificationManager has no static Show(); the call must be an
        # instance call on the object returned by CreateToastNotifier. The
        # static form compiles fine and only fails at runtime as
        # "does not contain a method named 'Show'" (found by a live smoke test).
        script = DesktopNotifier(system="windows")._command("title", "body")[-1]
        self.assertIn("$notifier.Show($toast)", script)
        self.assertNotIn("ToastNotificationManager]::Show", script)

    def test_unsupported_system_send_raises(self) -> None:
        with self.assertRaises(NotifierError):
            DesktopNotifier(system="plan9").send(batch())


if __name__ == "__main__":
    unittest.main()
