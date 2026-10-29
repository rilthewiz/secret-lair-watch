"""Best-effort desktop notifications (Windows toast, macOS, Linux).

Desktop notifications are cosmetic: failures never block the run and never stop
email delivery. Windows toasts use the built-in WinRT APIs through PowerShell, so
no extra packages are required.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from typing import List, Optional

from .base import NotificationBatch, Notifier, NotifierError

LOGGER = logging.getLogger(__name__)

WINDOWS_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:LAIRWATCH_TOAST_TITLE)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($env:LAIRWATCH_TOAST_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Secret Lair Watch')
# Show() is an instance method on the toast notifier. Calling it statically on
# ToastNotificationManager (the obvious-looking form) fails with "does not
# contain a method named 'Show'" on Windows PowerShell 5.1.
$notifier.Show($toast)
"""


class DesktopNotifier(Notifier):
    name = "desktop"

    def __init__(self, system: Optional[str] = None) -> None:
        self.system = (system or platform.system()).lower()

    def enabled(self) -> bool:
        if self.system == "windows":
            return shutil.which("powershell") is not None
        if self.system == "darwin":
            return shutil.which("osascript") is not None
        return shutil.which("notify-send") is not None

    def _command(self, title: str, body: str) -> List[str]:
        if self.system == "windows":
            return [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                WINDOWS_TOAST_SCRIPT,
            ]
        if self.system == "darwin":
            safe_title = title.replace('"', "'")
            safe_body = body.replace('"', "'")
            return [
                "osascript",
                "-e",
                f'display notification "{safe_body}" with title "{safe_title}"',
            ]
        return ["notify-send", title, body]

    def send(self, batch: NotificationBatch) -> None:
        if not self.enabled():
            raise NotifierError(
                f"no desktop notification command available on {self.system}"
            )

        title = "Secret Lair Watch"
        first = batch.notifications[0].headline()
        extra = len(batch.notifications) - 1
        body = first if not extra else f"{first} (+{extra} more)"

        environment = None
        if self.system == "windows":
            import os

            environment = dict(os.environ)
            environment["LAIRWATCH_TOAST_TITLE"] = title
            environment["LAIRWATCH_TOAST_BODY"] = body

        try:
            completed = subprocess.run(
                self._command(title, body),
                capture_output=True,
                text=True,
                timeout=20,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotifierError(f"desktop notification failed: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:300]
            raise NotifierError(
                f"desktop notification command exited {completed.returncode}: {detail}"
            )
        LOGGER.info("desktop notification shown: %s", body)
