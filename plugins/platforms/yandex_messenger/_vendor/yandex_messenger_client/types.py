"""Light-weight types and helpers for Bot API payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, TypedDict

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_PRIVATE_CHAT_ID_RE = re.compile(rf"^{_UUID}_{_UUID}$")


class Chat(TypedDict, total=False):
    type: str  # private | group | channel
    id: str


class Sender(TypedDict, total=False):
    id: str
    login: str
    display_name: str
    robot: bool


class File(TypedDict, total=False):
    id: str
    name: str
    size: int


class Image(TypedDict, total=False):
    file_id: str
    width: int
    height: int
    size: int
    name: str


class ServerAction(TypedDict, total=False):
    name: str
    payload: Any


class BotRequest(TypedDict, total=False):
    server_action: ServerAction
    element_id: str
    errors: list


class Update(TypedDict, total=False):
    update_id: int
    message_id: int
    timestamp: int
    chat: Chat
    text: str
    file: File
    images: list
    sticker: dict
    forwarded_messages: list
    reply_to_message: dict
    bot_request: BotRequest
    reaction: dict
    chat_members_update: dict


@dataclass(frozen=True)
class Target:
    """Exactly one of ``chat_id`` / ``login`` / ``user_id`` (the API rejects several)."""

    chat_id: Optional[str] = None
    login: Optional[str] = None
    user_id: Optional[str] = None

    def __post_init__(self) -> None:
        given = [v for v in (self.chat_id, self.login, self.user_id) if v]
        if len(given) != 1:
            raise ValueError("Target requires exactly one of chat_id, login, user_id")

    @classmethod
    def chat(cls, chat_id: str) -> "Target":
        return cls(chat_id=str(chat_id))

    @classmethod
    def for_login(cls, login: str) -> "Target":
        return cls(login=str(login))

    @classmethod
    def for_user(cls, user_id: str) -> "Target":
        return cls(user_id=str(user_id))

    @classmethod
    def parse(cls, value: "str | Target") -> "Target":
        """Parse a user-supplied string.

        Explicit prefixes ``chat:`` / ``login:`` / ``user:`` win. Otherwise a value
        containing ``/`` (group ``0/0/<uuid>``) or shaped like a private chat id
        (``<user-guid>_<bot-guid>``, verified against the live API) is a ``chat_id``;
        anything else is a ``login``.
        """
        if isinstance(value, Target):
            return value
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("empty target")
        for prefix, factory in (("chat:", cls.chat), ("login:", cls.for_login), ("user:", cls.for_user)):
            if raw.startswith(prefix):
                return factory(raw[len(prefix):].strip())
        if "/" in raw or _PRIVATE_CHAT_ID_RE.match(raw):
            return cls.chat(raw)
        return cls.for_login(raw)

    def as_params(self) -> dict:
        if self.chat_id:
            return {"chat_id": self.chat_id}
        if self.login:
            return {"login": self.login}
        return {"user_id": self.user_id}


def pick_original(images_variants: Any) -> Optional[Image]:
    """Choose the original from ``Update.images`` (``Image[][]`` or a flat list).

    The original carries ``size``/``name``; thumbnails do not. Fallback: the variant
    with the largest ``width * height``.
    """
    flat: list = []
    for item in images_variants or []:
        if isinstance(item, (list, tuple)):
            flat.extend(x for x in item if isinstance(x, dict))
        elif isinstance(item, dict):
            flat.append(item)
    flat = [img for img in flat if img.get("file_id")]
    if not flat:
        return None
    for img in flat:
        if img.get("size") or img.get("name"):
            return img  # type: ignore[return-value]
    return max(flat, key=lambda i: int(i.get("width") or 0) * int(i.get("height") or 0))  # type: ignore[return-value]


def pick_all_originals(images_variants: Any) -> list:
    """One original per image *group* (each inner list is the size variants of one picture)."""
    out: list = []
    for group in images_variants or []:
        original = pick_original([group] if isinstance(group, (list, tuple)) else [[group]])
        if original is not None:
            out.append(original)
    return out
