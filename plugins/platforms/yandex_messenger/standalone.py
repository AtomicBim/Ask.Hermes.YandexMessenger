"""Out-of-process delivery for cron jobs that run without a co-resident gateway."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from gateway.platforms._shared import send_error

from ._vendor.yandex_messenger_client import (
    Target,
    YMApiError,
    YandexMessengerClient,
    split_text,
    to_yandex_markdown,
)
from .settings import resolve_token

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _media_path(item: Any) -> Optional[str]:
    """``media_files`` entries are paths or ``(path, is_voice)`` tuples."""
    path = item[0] if isinstance(item, (tuple, list)) else item
    return str(path) if path else None


async def standalone_send(
    pconfig: Any, chat_id: str, message: str, *, thread_id: Any = None, media_files: Any = None,
    force_document: bool = False,
) -> dict:
    """``standalone_sender_fn``: send text (+ attachments) with a private client and close it."""
    token = resolve_token(pconfig)
    if not token or not chat_id:
        return send_error("Yandex Messenger standalone send: missing token or chat_id")
    try:
        target = Target.parse(chat_id)
    except ValueError as exc:
        return send_error(f"Yandex Messenger standalone send: {exc}")
    thread = int(thread_id) if str(thread_id or "").isdigit() else None
    last_id = None
    client = YandexMessengerClient(token)
    try:
        for chunk in split_text(to_yandex_markdown(message or "")):
            result = await client.send_text(target, chunk, thread_id=thread)
            last_id = result.get("message_id")
        for item in media_files or []:
            path = _media_path(item)
            if not path or not os.path.isfile(path):
                logger.warning("Yandex Messenger standalone: attachment missing: %s", path)
                continue
            as_image = os.path.splitext(path)[1].lower() in _IMAGE_EXTS and not force_document
            sender = client.send_image if as_image else client.send_file
            from .adapter import display_filename

            result = await sender(target, path, display_filename(path), thread_id=thread)
            last_id = result.get("message_id", last_id)
    except YMApiError as exc:
        return send_error(str(exc))
    except OSError as exc:
        return send_error(f"Yandex Messenger standalone send: {exc}")
    finally:
        await client.close()
    return {"success": True, "platform": "yandex_messenger", "chat_id": chat_id, "message_id": last_id}
