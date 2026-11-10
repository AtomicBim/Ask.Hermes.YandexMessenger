"""Yandex Messenger Bot API client (pure aiohttp, no other dependencies)."""

from .buttons import (
    action_card,
    empty_keyboard,
    keyboard,
    server_action_button,
    url_button,
)
from .client import DEFAULT_BASE_URL, MAX_TEXT_LENGTH, YandexMessengerClient
from .errors import (
    ERROR_KINDS,
    YMApiError,
    YMBadRequest,
    YMFileTooLarge,
    YMForbidden,
    YMNotFound,
    YMRateLimited,
    YMTransportError,
    classify,
)
from .formatting import split_text, to_yandex_markdown
from .types import Target, pick_all_originals, pick_original

__all__ = [
    "DEFAULT_BASE_URL",
    "ERROR_KINDS",
    "MAX_TEXT_LENGTH",
    "Target",
    "YMApiError",
    "YMBadRequest",
    "YMFileTooLarge",
    "YMForbidden",
    "YMNotFound",
    "YMRateLimited",
    "YMTransportError",
    "YandexMessengerClient",
    "action_card",
    "classify",
    "empty_keyboard",
    "keyboard",
    "pick_all_originals",
    "pick_original",
    "server_action_button",
    "split_text",
    "to_yandex_markdown",
    "url_button",
]
