"""Typed settings resolved from env (scoped secrets) and ``PlatformConfig.extra``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from gateway.platforms._shared import coerce_port, decode_json_list_literal, extra_or_secret, get_scoped_secret

DEFAULT_WEBHOOK_PORT = 8647
DEFAULT_WEBHOOK_PATH = "/yandex-messenger/webhook"
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_MAX_FILE_MB = 50.0
DEFAULT_EDIT_BUDGET_SECS = 1.0

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def as_float(value: Any, default: float, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(minimum, number)


def as_csv_set(value: Any, *, lower: bool = False) -> frozenset:
    value = decode_json_list_literal(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [str(v).strip() for v in value]
    else:
        items = [part.strip() for part in str(value or "").split(",")]
    return frozenset((i.lower() if lower else i) for i in items if i)


@dataclass(frozen=True)
class Settings:
    token: str = ""
    poll_interval: float = DEFAULT_POLL_INTERVAL
    webhook_url: str = ""
    webhook_host: Optional[str] = None
    webhook_port: int = DEFAULT_WEBHOOK_PORT
    webhook_path: str = DEFAULT_WEBHOOK_PATH
    webhook_secret: str = ""
    max_file_mb: float = DEFAULT_MAX_FILE_MB
    reactions: bool = True
    reaction_ack: str = "eyes"
    reaction_ok: str = "like"
    reaction_fail: str = "dislike"
    require_mention: bool = True
    allowed_chats: frozenset = field(default_factory=frozenset)
    edit_budget_secs: float = DEFAULT_EDIT_BUDGET_SECS

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)

    @property
    def webhook_mode(self) -> bool:
        return bool(self.webhook_url)


def resolve_token(config: Any = None) -> str:
    extra = getattr(config, "extra", None) or {}
    token = (
        get_scoped_secret("YANDEX_MESSENGER_BOT_TOKEN")
        or getattr(config, "token", None)
        or extra.get("bot_token")
        or ""
    )
    return str(token).strip()


API_HOST = "botapi.messenger.yandex.net"
CDN_HOST = "storage.mds.yandex.net"  # file downloads may be redirected here


def resolve_proxy(config: Any = None) -> Optional[str]:
    """Outbound proxy for every Yandex call (API, file download, CDN, cron delivery).

    Order (Hermes' standard ``resolve_proxy_url``): ``YANDEX_MESSENGER_PROXY`` (env / ``extra.proxy``), then
    ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``ALL_PROXY`` when ``gateway.trust_env`` is true; ``NO_PROXY``
    matching the API host disables it. Accepts ``http(s)://[user:pass@]host:port`` and ``socks5(h)://``.
    """
    from gateway.platforms.base import resolve_proxy_url

    extra = getattr(config, "extra", None) or {}
    configured = extra.get("proxy") or extra.get("proxy_url")
    return resolve_proxy_url("YANDEX_MESSENGER_PROXY", target_hosts=[API_HOST, CDN_HOST], configured=configured)


def redact_proxy(url: str) -> str:
    """``scheme://***@host:port`` - proxy URLs may embed credentials and must never reach the logs."""
    text = str(url or "")
    scheme, sep, rest = text.partition("://")
    if not sep:
        return "***"
    return f"{scheme}://" + (f"***@{rest.rsplit('@', 1)[1]}" if "@" in rest else rest)


def normalize_path(path: Any) -> str:
    text = str(path or "").strip() or DEFAULT_WEBHOOK_PATH
    return "/" + text.strip("/")


def load_settings(config: Any = None) -> Settings:
    """Env wins over ``extra`` (the documented Hermes precedence); blanks are unset."""
    extra = getattr(config, "extra", None) or {}

    def read(key: str, env: str, default: Any = "") -> Any:
        return extra_or_secret(extra, key, env, default)

    host = str(read("webhook_host", "YANDEX_MESSENGER_WEBHOOK_HOST", "") or "").strip() or None
    return Settings(
        token=resolve_token(config),
        poll_interval=as_float(read("poll_interval", "YANDEX_MESSENGER_POLL_INTERVAL", DEFAULT_POLL_INTERVAL),
                               DEFAULT_POLL_INTERVAL, minimum=0.2),
        webhook_url=str(read("webhook_url", "YANDEX_MESSENGER_WEBHOOK_URL", "") or "").strip().rstrip("/"),
        webhook_host=host,
        webhook_port=coerce_port(read("webhook_port", "YANDEX_MESSENGER_WEBHOOK_PORT", DEFAULT_WEBHOOK_PORT),
                                 DEFAULT_WEBHOOK_PORT),
        webhook_path=normalize_path(read("webhook_path", "YANDEX_MESSENGER_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH)),
        webhook_secret=str(read("webhook_secret", "YANDEX_MESSENGER_WEBHOOK_SECRET", "") or "").strip(),
        max_file_mb=as_float(read("max_file_mb", "YANDEX_MESSENGER_MAX_FILE_MB", DEFAULT_MAX_FILE_MB),
                             DEFAULT_MAX_FILE_MB, minimum=0.1),
        reactions=as_bool(read("reactions", "YANDEX_MESSENGER_REACTIONS", True), True),
        reaction_ack=str(read("reaction_ack", "YANDEX_MESSENGER_REACTION_ACK", "eyes")).strip(),
        reaction_ok=str(read("reaction_ok", "YANDEX_MESSENGER_REACTION_OK", "like")).strip(),
        reaction_fail=str(read("reaction_fail", "YANDEX_MESSENGER_REACTION_FAIL", "dislike")).strip(),
        require_mention=as_bool(read("require_mention", "YANDEX_MESSENGER_REQUIRE_MENTION", True), True),
        allowed_chats=as_csv_set(read("allowed_chats", "YANDEX_MESSENGER_ALLOWED_CHATS", "")),
        edit_budget_secs=as_float(extra.get("edit_budget_secs", DEFAULT_EDIT_BUDGET_SECS),
                                  DEFAULT_EDIT_BUDGET_SECS),
    )
