"""Configuration loading, environment overrides, and validation.

Configuration is read from a JSON file. Secrets are never stored in the file:
the email notifier references the *names* of environment variables that hold
credentials.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schedule import DropReminder, ScheduleError, parse_schedule

DEFAULT_USER_AGENT = (
    "secret-lair-watch/0.1 (+personal notification tool; "
    "contact: set-user-agent-in-config)"
)

KNOWN_SOURCE_TYPES = ("secret_lair_store", "wotc_sitemap")

# Hard floor for polling. This is intentionally conservative: the storefront is
# a live commerce site and hammers poorly under automated polling.
HARD_MIN_INTERVAL_SECONDS = 600

DEFAULT_CONFIG: Dict[str, Any] = {
    "user_agent": DEFAULT_USER_AGENT,
    "interval_seconds": 3600,
    "min_interval_seconds": 900,
    "jitter_ratio": 0.1,
    "request_timeout_seconds": 30,
    "retry_delay_seconds": 5,
    "state_path": "state/state.json",
    "log_level": "INFO",
    "log_file": "logs/lairwatch.log",
    "respect_robots_txt": True,
    "robots_cache_seconds": 86400,
    "prune_after_days": 365,
    "sources": [
        {
            "name": "secret_lair_store",
            "type": "secret_lair_store",
            "enabled": True,
            "url": "https://secretlair.wizards.com/us",
            "notify_on_status_change": True,
            "alert_on_first_run": False,
        },
        {
            "name": "wotc_secret_lair_news",
            "type": "wotc_sitemap",
            "enabled": True,
            "url": "https://magic.wizards.com/en/sitemap.xml",
            "url_pattern": r"/en/news/.*secret-lair",
            "notify_on_status_change": False,
            "alert_on_first_run": False,
        },
    ],
    "remind_open_browser": False,
    "remind_max_wait_hours": 72,
    "remind_sound": True,
    "remind_sound_file": None,
    "drop_schedule": [],
    "notifiers": {
        "console": {"enabled": True},
        "desktop": {"enabled": True},
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "starttls": True,
            "from_addr": "",
            "to_addrs": [],
            "username_env": "LAIRWATCH_SMTP_USER",
            "password_env": "LAIRWATCH_SMTP_PASSWORD",
        },
    },
}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class SourceConfig:
    name: str
    type: str
    url: str
    enabled: bool = True
    notify_on_status_change: bool = False
    alert_on_first_run: bool = False
    url_pattern: Optional[str] = None


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    starttls: bool = True
    from_addr: str = ""
    to_addrs: List[str] = field(default_factory=list)
    username_env: str = "LAIRWATCH_SMTP_USER"
    password_env: str = "LAIRWATCH_SMTP_PASSWORD"


@dataclass
class NotifierConfig:
    console_enabled: bool = True
    desktop_enabled: bool = True
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class Config:
    user_agent: str = DEFAULT_USER_AGENT
    interval_seconds: int = 3600
    min_interval_seconds: int = 900
    jitter_ratio: float = 0.1
    request_timeout_seconds: int = 30
    retry_delay_seconds: int = 5
    state_path: Path = Path("state/state.json")
    log_level: str = "INFO"
    log_file: Optional[Path] = Path("logs/lairwatch.log")
    respect_robots_txt: bool = True
    robots_cache_seconds: int = 86400
    prune_after_days: int = 365
    remind_open_browser: bool = False
    remind_max_wait_hours: int = 72
    remind_sound: bool = True
    remind_sound_file: Optional[str] = None
    drop_schedule: List["DropReminder"] = field(default_factory=list)
    sources: List[SourceConfig] = field(default_factory=list)
    notifiers: NotifierConfig = field(default_factory=NotifierConfig)


    @property
    def effective_interval(self) -> int:
        return max(self.interval_seconds, self.min_interval_seconds)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _env_bool(name: str) -> Optional[bool]:
    value = _env(name)
    if value is None:
        return None
    return _bool(value)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_config_file(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a JSON object")
    return data


def _parse_sources(raw_sources: Any) -> List[SourceConfig]:
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError("'sources' must be a non-empty list")

    sources: List[SourceConfig] = []
    seen = set()
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise ConfigError(f"sources[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        source_type = str(raw.get("type") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not name:
            raise ConfigError(f"sources[{index}] is missing 'name'")
        if name in seen:
            raise ConfigError(f"duplicate source name: {name}")
        if source_type not in KNOWN_SOURCE_TYPES:
            raise ConfigError(
                f"source {name!r} has unknown type {source_type!r}; "
                f"expected one of {', '.join(KNOWN_SOURCE_TYPES)}"
            )
        if not url.startswith(("https://", "http://")):
            raise ConfigError(f"source {name!r} has an invalid url: {url!r}")
        seen.add(name)
        sources.append(
            SourceConfig(
                name=name,
                type=source_type,
                url=url,
                enabled=_bool(raw.get("enabled"), True),
                notify_on_status_change=_bool(raw.get("notify_on_status_change"), False),
                alert_on_first_run=_bool(raw.get("alert_on_first_run"), False),
                url_pattern=raw.get("url_pattern"),
            )
        )
    return sources


def _parse_notifiers(raw: Any) -> NotifierConfig:
    if raw is None:
        return NotifierConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'notifiers' must be an object")

    email_raw = raw.get("email") or {}
    if not isinstance(email_raw, dict):
        raise ConfigError("'notifiers.email' must be an object")

    to_addrs = email_raw.get("to_addrs") or []
    if isinstance(to_addrs, str):
        to_addrs = [part.strip() for part in to_addrs.split(",") if part.strip()]
    if not isinstance(to_addrs, list):
        raise ConfigError("'notifiers.email.to_addrs' must be a list of addresses")

    try:
        smtp_port = int(email_raw.get("smtp_port", 587))
    except (TypeError, ValueError) as exc:
        raise ConfigError("'notifiers.email.smtp_port' must be an integer") from exc

    email = EmailConfig(
        enabled=_bool(email_raw.get("enabled"), False),
        smtp_host=str(email_raw.get("smtp_host") or ""),
        smtp_port=smtp_port,
        starttls=_bool(email_raw.get("starttls"), True),
        from_addr=str(email_raw.get("from_addr") or ""),
        to_addrs=[str(addr) for addr in to_addrs],
        username_env=str(email_raw.get("username_env") or "LAIRWATCH_SMTP_USER"),
        password_env=str(email_raw.get("password_env") or "LAIRWATCH_SMTP_PASSWORD"),
    )
    if email.enabled:
        missing = []
        if not email.smtp_host:
            missing.append("smtp_host")
        if not email.from_addr:
            missing.append("from_addr")
        if not email.to_addrs:
            missing.append("to_addrs")
        if missing:
            raise ConfigError(
                "email notifier is enabled but missing: " + ", ".join(missing)
            )

    console_raw = raw.get("console") or {}
    desktop_raw = raw.get("desktop") or {}
    return NotifierConfig(
        console_enabled=_bool(console_raw.get("enabled"), True),
        desktop_enabled=_bool(desktop_raw.get("enabled"), True),
        email=email,
    )


def _parse_schedule(raw: Any) -> List[DropReminder]:
    try:
        return parse_schedule(raw)
    except ScheduleError as exc:
        raise ConfigError(str(exc)) from exc


def _apply_env(config: Config) -> Config:
    """Apply documented environment overrides on top of the file config."""
    if (value := _env("LAIRWATCH_INTERVAL_SECONDS")) is not None:
        try:
            config.interval_seconds = int(value)
        except ValueError as exc:
            raise ConfigError("LAIRWATCH_INTERVAL_SECONDS must be an integer") from exc
    if (value := _env("LAIRWATCH_STATE_PATH")) is not None:
        config.state_path = Path(value)
    if (value := _env("LAIRWATCH_LOG_LEVEL")) is not None:
        config.log_level = value.upper()
    if (value := _env("LAIRWATCH_LOG_FILE")) is not None:
        config.log_file = Path(value)
    if (value := _env("LAIRWATCH_USER_AGENT")) is not None:
        config.user_agent = value
    if (value := _env_bool("LAIRWATCH_DESKTOP_ENABLED")) is not None:
        config.notifiers.desktop_enabled = value
    if (value := _env_bool("LAIRWATCH_EMAIL_ENABLED")) is not None:
        config.notifiers.email.enabled = value
    if (value := _env("LAIRWATCH_SMTP_HOST")) is not None:
        config.notifiers.email.smtp_host = value
    if (value := _env("LAIRWATCH_SMTP_PORT")) is not None:
        try:
            config.notifiers.email.smtp_port = int(value)
        except ValueError as exc:
            raise ConfigError("LAIRWATCH_SMTP_PORT must be an integer") from exc
    if (value := _env("LAIRWATCH_SMTP_FROM")) is not None:
        config.notifiers.email.from_addr = value
    if (value := _env("LAIRWATCH_SMTP_TO")) is not None:
        config.notifiers.email.to_addrs = [
            part.strip() for part in value.split(",") if part.strip()
        ]
    if (value := _env_bool("LAIRWATCH_SMTP_STARTTLS")) is not None:
        config.notifiers.email.starttls = value
    if (value := _env_bool("LAIRWATCH_OPEN_BROWSER")) is not None:
        config.remind_open_browser = value

    if config.notifiers.email.enabled:
        missing = []
        if not config.notifiers.email.smtp_host:
            missing.append("smtp_host")
        if not config.notifiers.email.from_addr:
            missing.append("from_addr")
        if not config.notifiers.email.to_addrs:
            missing.append("to_addrs")
        if missing:
            raise ConfigError(
                "email notifier is enabled but missing: " + ", ".join(missing)
            )
    return config


def _validate(config: Config) -> Config:
    if config.min_interval_seconds < HARD_MIN_INTERVAL_SECONDS:
        raise ConfigError(
            f"min_interval_seconds must be >= {HARD_MIN_INTERVAL_SECONDS}; "
            "this app is deliberately conservative"
        )
    if config.interval_seconds < HARD_MIN_INTERVAL_SECONDS:
        raise ConfigError(
            f"interval_seconds must be >= {HARD_MIN_INTERVAL_SECONDS}; "
            "this app is deliberately conservative"
        )
    if config.interval_seconds < config.min_interval_seconds:
        config.interval_seconds = config.min_interval_seconds
    if not (0 <= config.jitter_ratio <= 0.5):
        raise ConfigError("jitter_ratio must be between 0 and 0.5")
    if config.request_timeout_seconds < 5:
        raise ConfigError("request_timeout_seconds must be >= 5")
    if config.prune_after_days < 1:
        raise ConfigError("prune_after_days must be >= 1")
    if config.remind_max_wait_hours < 1:
        raise ConfigError("remind_max_wait_hours must be >= 1")
    if not config.user_agent.strip():
        raise ConfigError("user_agent must not be empty")
    if "Mozilla/" in config.user_agent:
        raise ConfigError(
            "user_agent must identify this app, not impersonate a browser"
        )
    if not config.sources:
        raise ConfigError("at least one source is required")
    return config


def load_config(path: Optional[Path] = None) -> Config:
    """Load config from *path* (JSON), merge defaults, then env overrides."""
    explicit_path = path or (Path(_env("LAIRWATCH_CONFIG")) if _env("LAIRWATCH_CONFIG") else None)
    raw: Dict[str, Any] = {}
    if explicit_path is not None:
        raw = _read_config_file(Path(explicit_path))

    merged = _deep_merge(DEFAULT_CONFIG, raw)

    try:
        interval_seconds = int(merged.get("interval_seconds", 3600))
        min_interval_seconds = int(merged.get("min_interval_seconds", 900))
        request_timeout_seconds = int(merged.get("request_timeout_seconds", 30))
        retry_delay_seconds = int(merged.get("retry_delay_seconds", 5))
        robots_cache_seconds = int(merged.get("robots_cache_seconds", 86400))
        prune_after_days = int(merged.get("prune_after_days", 365))
        jitter_ratio = float(merged.get("jitter_ratio", 0.1))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"numeric config value is invalid: {exc}") from exc

    log_file_raw = merged.get("log_file")
    config = Config(
        user_agent=str(merged.get("user_agent") or DEFAULT_USER_AGENT),
        interval_seconds=interval_seconds,
        min_interval_seconds=min_interval_seconds,
        jitter_ratio=jitter_ratio,
        request_timeout_seconds=request_timeout_seconds,
        retry_delay_seconds=retry_delay_seconds,
        state_path=Path(str(merged.get("state_path") or "state/state.json")),
        log_level=str(merged.get("log_level") or "INFO").upper(),
        log_file=Path(str(log_file_raw)) if log_file_raw else None,
        respect_robots_txt=_bool(merged.get("respect_robots_txt"), True),
        robots_cache_seconds=robots_cache_seconds,
        prune_after_days=prune_after_days,
        remind_open_browser=_bool(merged.get("remind_open_browser"), False),
        remind_max_wait_hours=int(merged.get("remind_max_wait_hours", 72)),
        remind_sound=_bool(merged.get("remind_sound"), True),
        remind_sound_file=(
            str(merged.get("remind_sound_file"))
            if merged.get("remind_sound_file")
            else None
        ),
        drop_schedule=_parse_schedule(merged.get("drop_schedule")),
        sources=_parse_sources(merged.get("sources")),
        notifiers=_parse_notifiers(merged.get("notifiers")),
    )
    return _validate(_apply_env(config))

