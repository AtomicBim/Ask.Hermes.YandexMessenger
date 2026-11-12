"""Yandex Messenger (Bot API) platform adapter for Hermes.

Transport: ``getUpdates`` polling (default) or webhook (``YANDEX_MESSENGER_WEBHOOK_URL``).
Parity with the Telegram adapter: streaming by edits, typing/status, media in/out, tappable
approval / clarify / slash-confirm / pickers, reactions, group mention gating, cron delivery.

Addressing: every reply goes to ``chat.id`` (valid as ``chat_id`` for private chats too).
Strings coming from outside (home channel, cron, send_message) are parsed by ``Target.parse``:
``chat:<id>`` / ``login:<x>`` / ``user:<guid>`` explicitly, ``.../...`` is a chat id, else a login.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Optional

from gateway.platforms._shared import get_scoped_secret
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    cache_image_from_url,
    cache_media_bytes_async,
)
from gateway.platforms.event import MessageEvent, MessageType
from gateway.platforms.helpers import MessageDeduplicator

from ._vendor.yandex_messenger_client import (
    MAX_TEXT_LENGTH,
    Target,
    YMApiError,
    YMBadRequest,
    YMFileTooLarge,
    YMNotFound,
    YMRateLimited,
    YMTransportError,
    YandexMessengerClient,
    pick_all_originals,
    split_text,
    to_yandex_markdown,
)
from . import gateway_env
from .interactive import InteractiveMixin
from .settings import Settings, load_settings, redact_proxy, resolve_proxy
from .standalone import standalone_send
from .transport import OffsetStore, PollingLoop, WebhookServer

logger = logging.getLogger(__name__)

PLATFORM_NAME = "yandex_messenger"
_VOICE_EXTS = {".ogg", ".opus"}
_TEXT_INLINE_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".xml", ".py", ".html", ".ini", ".toml"}
_TEXT_INLINE_MAX_BYTES = 100 * 1024
_FLOOD_INLINE_WAIT_CAP_SECS = 5.0
_TYPING_SECONDS = 5


# Hermes' media cache stores files as ``doc_<12 hex>_<original name>`` (``img_``/``audio_``/``video_`` for
# the other kinds). The prefix is an implementation detail: never show it to the user.
_CACHE_PREFIX_RE = re.compile(r"^(?:doc|img|image|audio|video)_[0-9a-f]{12}_(?=.)")


def display_filename(path: str) -> str:
    """Basename of ``path`` without Hermes' cache prefix (a file we received and the agent sends back)."""
    name = os.path.basename(path)
    return _CACHE_PREFIX_RE.sub("", name, count=1) or name


def _loop_time() -> float:
    return asyncio.get_running_loop().time()


def _hermes_state_dir() -> Optional[Path]:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "yandex_messenger"
    except Exception:
        return None


class YandexMessengerAdapter(InteractiveMixin, BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = MAX_TEXT_LENGTH
    splits_long_messages = True
    supports_code_blocks = True
    supports_status_text = True
    REQUIRES_EDIT_FINALIZE = False
    serves_profile_prefix = True  # webhook mode is served via the shared ingress

    def __init__(self, config, **kwargs):
        super().__init__(config=config, platform=gateway_env.platform())
        self._settings: Settings = load_settings(config)
        self._reply_mode = str(getattr(config, "reply_to_mode", "first") or "first").lower()
        self._reply_in_dm = bool((getattr(config, "extra", None) or {}).get("reply_in_dm", False))
        self._client: Optional[YandexMessengerClient] = None
        self._poller: Optional[PollingLoop] = None
        self._webhook: Optional[WebhookServer] = None
        self._poll_offset = 0
        self._bot_id: str = ""
        self._bot_login: str = ""
        # at-least-once webhook delivery: de-duplicate on update_id (bounded by max_size)
        self._dedup = MessageDeduplicator(max_size=2000, ttl_seconds=86400)
        self._chat_types: dict = {}          # chat_id -> private|group
        self._chat_info_cache: dict = {}
        self._chat_locks: dict = {}
        self._slot_until: dict = {}          # chat_id -> loop time the next outbound may fire
        self._last_overflow_preview: dict = {}
        self._typing_cooldown: dict = {}
        self._reaction_names: dict = {}      # chat_id -> frozenset of available reaction names
        self._reactions_broken = False
        self._tasks: set = set()
        self._init_interactive_state()
        # base on_processing_complete swaps reactions when these are set
        self._ACK_EMOJI = self._settings.reaction_ack or None
        self._OK_EMOJI = self._settings.reaction_ok or None
        self._FAIL_EMOJI = self._settings.reaction_fail or None

    # ------------------------------------------------------------------ helpers
    @property
    def token(self) -> str:
        return self._settings.token

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _fail(self, code: str, detail: str, *, retryable: bool = False) -> bool:
        self._set_fatal_error(code, detail, retryable=retryable)
        return False

    def _target(self, chat_id: Any) -> Target:
        text = str(chat_id)
        if text in self._chat_types:
            return Target.chat(text)
        return Target.parse(text)

    @staticmethod
    def _thread_id(metadata: Optional[dict]) -> Optional[int]:
        raw = (metadata or {}).get("thread_id")
        try:
            return int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _is_private(self, chat_id: Any) -> bool:
        return self._chat_types.get(str(chat_id)) == "private"

    def _reply_anchor(self, chat_id: Any, reply_to: Optional[str]) -> Optional[int]:
        """Quote-replies follow ``reply_to_mode``; in private chats they are off unless ``reply_in_dm``
        is set (every answer quoting the question is noise in a one-to-one chat)."""
        if not reply_to or self._reply_mode == "off":
            return None
        if self._is_private(chat_id) and not self._reply_in_dm:
            return None
        return int(reply_to) if str(reply_to).isdigit() else None

    def _error_result(self, exc: Exception) -> SendResult:
        """Map a client exception to a ``SendResult`` Hermes' retry/stream logic understands."""
        if isinstance(exc, YMRateLimited):
            wait = exc.retry_after or 1.0
            return SendResult(
                success=False, error=f"Yandex rate limit: retry after {wait:g}s", retry_after=wait,
                error_kind="rate_limited", retryable=True,
            )
        if isinstance(exc, YMFileTooLarge):
            return SendResult(success=False, error="file too large", error_kind="too_long")
        if isinstance(exc, YMTransportError):
            connect_level = any(m in str(exc) for m in ("ClientConnector", "Cannot connect", "ConnectionRefused"))
            return SendResult(success=False, error=str(exc), error_kind="transient", retryable=connect_level)
        kind = getattr(exc, "kind", None) or "unknown"
        if kind == "too_long":
            return SendResult(success=False, error="message_too_long", error_kind="too_long")
        return SendResult(success=False, error=str(exc), error_kind=kind)

    # ------------------------------------------------------------------ outbound pacing
    def _slot_remaining(self, chat_id: Any) -> float:
        deadline = self._slot_until.get(str(chat_id))
        if deadline is None:
            return 0.0
        remaining = deadline - _loop_time()
        if remaining <= 0:
            self._slot_until.pop(str(chat_id), None)
            return 0.0
        return remaining

    def _hold_slot(self, chat_id: Any) -> None:
        self._slot_until[str(chat_id)] = _loop_time() + max(0.0, self._settings.edit_budget_secs)
        if len(self._slot_until) > 2000:  # bounded
            for key in list(self._slot_until)[:1000]:
                self._slot_until.pop(key, None)

    def _chat_lock(self, chat_id: Any) -> asyncio.Lock:
        lock = self._chat_locks.get(str(chat_id))
        if lock is None:
            if len(self._chat_locks) > 2000:
                self._chat_locks = {k: v for k, v in self._chat_locks.items() if v.locked()}
            lock = self._chat_locks[str(chat_id)] = asyncio.Lock()
        return lock

    # ------------------------------------------------------------------ lifecycle
    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.token:
            return self._fail("config_missing", "YANDEX_MESSENGER_BOT_TOKEN must be set")
        identity = hashlib.sha256(self.token.encode()).hexdigest()[:16]
        if not self._acquire_platform_lock("yandex-messenger-token", identity, "Yandex Messenger bot token"):
            return False
        settings = self._settings
        if settings.webhook_mode and not settings.webhook_secret:
            self._release_platform_lock()
            return self._fail(
                "webhook_secret_missing",
                "YANDEX_MESSENGER_WEBHOOK_SECRET must be set in webhook mode (the Bot API does not sign "
                "webhook calls; the secret in the URL is the only authentication)",
            )
        self._log_command_policy()
        await self._close_client()
        # The proxy is resolved once, explicitly (YANDEX_MESSENGER_PROXY > HTTPS_PROXY/ALL_PROXY, NO_PROXY honoured),
        # so the client itself never reads the environment (trust_env stays False).
        proxy = resolve_proxy(self.config)
        if proxy:
            logger.info("[%s] using outbound proxy %s", self.name, redact_proxy(proxy))
        self._client = YandexMessengerClient(self.token, proxy=proxy)
        try:
            me = await self._client.get_self()
        except YMApiError as exc:
            await self._close_client()
            self._release_platform_lock()
            if exc.kind == "forbidden":
                return self._fail("auth_failed", f"Yandex Messenger rejected the bot token: {exc}")
            return self._fail("connect_failed", f"Yandex Messenger get_self failed: {exc}", retryable=True)
        self._bot_id = str(me.get("id") or "")
        self._bot_login = str(me.get("login") or "")
        try:
            if settings.webhook_mode:
                await self._start_webhook()
            else:
                await self._start_polling(me, is_reconnect=is_reconnect)
        except OSError as exc:
            await self._stop_transport()
            await self._close_client()
            self._release_platform_lock()
            return self._fail("bind_failed", f"Could not bind the Yandex Messenger webhook listener: {exc}",
                              retryable=True)
        except YMApiError as exc:
            await self._stop_transport()
            await self._close_client()
            self._release_platform_lock()
            return self._fail("connect_failed", f"Yandex Messenger setup failed: {exc}", retryable=True)
        self._wire_plugin_handlers(None)
        self._mark_connected()
        logger.info(
            "[%s] connected as %s (%s mode)", self.name, self._bot_login or self._bot_id,
            "webhook" if settings.webhook_mode else "polling",
        )
        return True

    def _log_command_policy(self) -> None:
        from gateway.slash_access import policy_from_extra

        policy = policy_from_extra(getattr(self.config, "extra", None) or {}, "dm")
        if policy.enabled:
            logger.info(
                "[%s] slash-command gating ON: %d admin(s); other users may run /help /whoami and %s",
                self.name, len(policy.admin_user_ids), ", ".join(f"/{c}" for c in sorted(policy.user_allowed_commands)) or "nothing else",
            )
        else:
            logger.warning(
                "[%s] slash-command gating OFF: every allowed user can run /model, /config, /yolo and other "
                "settings commands. Set YANDEX_MESSENGER_ADMIN_USERS to restrict them to administrators.", self.name,
            )

    async def _start_polling(self, me: dict, *, is_reconnect: bool) -> None:
        if me.get("webhook_url"):
            logger.warning(
                "[%s] the bot has a webhook configured (%s); polling gets nothing while it is set — resetting it",
                self.name, me.get("webhook_url"),
            )
            await self._client.update_self(webhook_url=None)
        store = None
        state_dir = _hermes_state_dir()
        if state_dir is not None:
            digest = hashlib.sha256(self.token.encode()).hexdigest()[:10]
            store = OffsetStore(state_dir / f"offset-{digest}.json")
        self._poller = PollingLoop(
            self._client, self._dispatch_update,
            poll_interval=self._settings.poll_interval, offset=self._poll_offset, store=store,
            on_fatal=self._on_poll_fatal,
        )
        self._poller.start()

    async def _on_poll_fatal(self, exc: YMApiError) -> None:
        self._set_fatal_error("auth_failed", f"Yandex Messenger polling failed: {exc}", retryable=False)
        await self._notify_fatal_error()

    async def _start_webhook(self) -> None:
        settings = self._settings
        server = WebhookServer(
            self, self._dispatch_update, host=settings.webhook_host, port=settings.webhook_port,
            path=settings.webhook_path, secret=settings.webhook_secret,
        )
        await server.start()
        self._webhook = server
        await self._client.update_self(webhook_url=server.public_url(settings.webhook_url))
        logger.info("[%s] webhook registered at %s/%s", self.name, settings.webhook_url, settings.webhook_path.strip("/"))

    async def _stop_transport(self) -> None:
        if self._poller is not None:
            self._poll_offset = self._poller.offset
            await self._poller.stop()
        if self._webhook is not None:
            with contextlib.suppress(Exception):
                await self._webhook.stop()
            self._webhook = None

    async def _close_client(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
            self._client = None

    async def disconnect(self) -> None:
        self._mark_disconnected()
        await self._stop_transport()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        await self._close_client()
        with contextlib.suppress(Exception):
            self._release_platform_lock()

    # ------------------------------------------------------------------ inbound
    def _is_self(self, sender: dict) -> bool:
        if sender.get("robot") is True:
            return True
        login = str(sender.get("login") or "")
        return bool(
            (self._bot_login and login and login.lower() == self._bot_login.lower())
            or (self._bot_id and str(sender.get("id") or "") == self._bot_id)
        )

    def _mention_regex(self) -> Optional["re.Pattern[str]"]:
        if not self._bot_login:
            return None
        names = {self._bot_login}
        if "@" in self._bot_login:
            names.add(self._bot_login.split("@", 1)[0])
        alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        return re.compile(rf"(?<![\w.@-])@(?:{alternation})(?![\w-])", re.IGNORECASE)

    def _replied_to_bot(self, update: dict) -> bool:
        author = (update.get("reply_to_message") or {}).get("from") or {}
        return bool(author) and self._is_self(author)

    def _group_gate(self, update: dict, text: str) -> Optional[str]:
        """Return the text to process, or ``None`` when a group message is not for the bot."""
        mention = self._mention_regex()
        mentioned = bool(mention and mention.search(text))
        if mention:
            text = mention.sub("", text)
            text = re.sub(r"[ \t]{2,}", " ", text)
            text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)   # "hey , x" -> "hey, x"
            text = re.sub(r"^[\s,:;]+", "", text).strip()      # "@bot, do x" -> "do x"
        is_command = text.startswith("/")
        if self._settings.require_mention and not (mentioned or is_command or self._replied_to_bot(update)):
            return None
        return text

    async def _dispatch_update(self, update: dict) -> None:
        if not isinstance(update, dict):
            return
        uid = update.get("update_id")
        if uid is not None and self._dedup.is_duplicate(str(uid)):
            return
        sender = update.get("from") or {}
        chat = update.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        chat_type = str(chat.get("type") or "private")
        if not chat_id or self._is_self(sender):
            return
        if "bot_request" in update:
            # tap handlers may await slow work (a confirmed slash command): never block the poll loop
            self._spawn(self._handle_bot_request(update))
            return
        if "reaction" in update or "chat_members_update" in update or chat_type == "channel":
            return
        self._chat_types[chat_id] = chat_type
        if chat_type == "group":
            allowed = self._settings.allowed_chats
            if allowed and chat_id not in allowed:
                logger.debug("[%s] ignoring group %s (not in allowed chats)", self.name, chat_id)
                return
        text = str(update.get("text") or "")
        if chat_type == "group":
            gated = self._group_gate(update, text)
            if gated is None:
                return
            text = gated
        await self._deliver_inbound(update, text, chat_id, chat_type, sender)

    async def _deliver_inbound(self, update: dict, text: str, chat_id: str, chat_type: str, sender: dict) -> None:
        login = str(sender.get("login") or "")
        user_id = login or str(sender.get("id") or "")
        user_name = str(sender.get("display_name") or login or user_id)
        text = re.sub(rf"^(/\w+)@{re.escape(self._bot_login)}\b", r"\1", text) if self._bot_login else text

        media_urls: list = []
        media_types: list = []
        inlined: list = []
        kinds: list = []
        notices: list = []
        sources = [update] + [m for m in (update.get("forwarded_messages") or []) if isinstance(m, dict)]
        for message in sources:
            text = await self._collect_media(message, chat_id, text, media_urls, media_types, inlined, kinds, notices)
        for message in sources[1:]:
            fwd_text = str(message.get("text") or "").strip()
            if fwd_text:
                who = (message.get("from") or {}).get("display_name") or (message.get("from") or {}).get("login") or "?"
                text = f"{text}\n\n[Forwarded from {who}]:\n{fwd_text}".strip()
        if update.get("sticker") and not text:
            text = "[sticker]"
        if notices:
            block = "[Yandex attachment notice]\n" + "\n".join(f"- {n}" for n in notices)
            text = f"{block}\n\n{text}" if text else block
        if not text and not media_urls:
            return

        if media_urls:
            kind = kinds[0] if kinds else "document"
            msg_type = {
                "image": MessageType.PHOTO, "video": MessageType.VIDEO,
                "audio": MessageType.VOICE if self._first_is_voice(media_urls) else MessageType.AUDIO,
            }.get(kind, MessageType.DOCUMENT)
        elif text.startswith("/"):
            msg_type = MessageType.COMMAND
        else:
            msg_type = MessageType.TEXT

        message_id = str(update.get("message_id") or update.get("update_id") or "")
        thread_id = str(update["thread_id"]) if update.get("thread_id") else None
        source = self.build_source(
            chat_id=chat_id, chat_type="dm" if chat_type == "private" else "group",
            user_id=user_id, user_name=user_name, user_id_alt=str(sender.get("id") or "") or None,
            thread_id=thread_id, message_id=message_id,
        )
        event = MessageEvent(
            text=text, message_type=msg_type, source=source, raw_message=update, message_id=message_id,
            user_id=user_id, user_name=user_name,
            platform_update_id=update.get("update_id") if isinstance(update.get("update_id"), int) else None,
            media_urls=media_urls, media_types=media_types, media_text_inlined=inlined,
        )
        replied = update.get("reply_to_message")
        if isinstance(replied, dict) and replied:
            author = replied.get("from") or {}
            event.reply_to_message_id = str(replied.get("message_id") or "") or None
            event.reply_to_text = str(replied.get("text") or "") or None
            event.reply_to_author_id = str(author.get("login") or author.get("id") or "") or None
            event.reply_to_author_name = str(author.get("display_name") or author.get("login") or "") or None
            event.reply_to_is_own_message = self._replied_to_bot(update)
        await self.handle_message(event)

    @staticmethod
    def _first_is_voice(paths: list) -> bool:
        return bool(paths) and os.path.splitext(paths[0])[1].lower() in _VOICE_EXTS

    async def _collect_media(
        self, message: dict, chat_id: str, text: str, urls: list, types: list, inlined: list,
        kinds: list, notices: list,
    ) -> str:
        """Download ``file`` and ``images`` of ``message`` into the media cache; returns updated text."""
        file_obj = message.get("file")
        if isinstance(file_obj, dict) and file_obj.get("id"):
            text = await self._ingest(
                file_obj["id"], str(file_obj.get("name") or ""), int(file_obj.get("size") or 0), None,
                chat_id, text, urls, types, inlined, kinds, notices,
            )
        for image in pick_all_originals(message.get("images")):
            text = await self._ingest(
                image["file_id"], str(image.get("name") or "image.jpg"), int(image.get("size") or 0), "image",
                chat_id, text, urls, types, inlined, kinds, notices,
            )
        return text

    async def _ingest(
        self, file_id: str, name: str, size: int, default_kind: Optional[str], chat_id: str, text: str,
        urls: list, types: list, inlined: list, kinds: list, notices: list,
    ) -> str:
        limit = self._settings.max_file_bytes
        label = name or "file"
        if size and size > limit:
            await self._tell_too_large(chat_id, label, size)
            notices.append(f"'{label}' skipped: {size / 1048576:.1f} MB exceeds the {self._settings.max_file_mb:g} MB limit")
            return text
        try:
            data = await self._client.get_file(file_id, max_bytes=limit)
        except YMFileTooLarge:
            await self._tell_too_large(chat_id, label, size)
            notices.append(f"'{label}' skipped: larger than {self._settings.max_file_mb:g} MB")
            return text
        except YMApiError as exc:
            logger.warning("[%s] downloading %s failed: %s", self.name, label, exc)
            notices.append(f"'{label}' could not be downloaded")
            return text
        mime = mimetypes.guess_type(name)[0] or ""
        try:
            cached = await cache_media_bytes_async(data, filename=name, mime_type=mime, default_kind=default_kind)
        except Exception as exc:
            logger.warning("[%s] caching %s failed: %s", self.name, label, exc)
            cached = None
        if cached is None:
            notices.append(f"'{label}' is not a valid file")
            return text
        urls.append(cached.path)
        types.append(cached.media_type)
        kinds.append(cached.kind)
        ext = os.path.splitext(name)[1].lower()
        injected = False
        if cached.kind == "document" and ext in _TEXT_INLINE_EXTS and len(data) <= _TEXT_INLINE_MAX_BYTES:
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                body = None
            if body is not None:
                text = f"[Content of {label}]:\n{body}\n\n{text}".strip()
                injected = True
        inlined.append(injected)
        return text

    async def _tell_too_large(self, chat_id: str, label: str, size: int) -> None:
        note = (
            f"Файл «{label}» слишком большой"
            + (f" ({size / 1048576:.1f} МБ)" if size else "")
            + f" — максимум {self._settings.max_file_mb:g} МБ. Отправьте файл поменьше."
        )
        with contextlib.suppress(Exception):
            await self.send(chat_id, note)

    # ------------------------------------------------------------------ outbound: text
    async def send(
        self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[dict] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Not connected", retryable=True)
        if not content or not content.strip():
            return SendResult(success=True, message_id=None)
        async with self._chat_lock(chat_id):
            return await self._send_locked(chat_id, content, reply_to, metadata)

    async def _send_locked(
        self, chat_id: str, content: str, reply_to: Optional[str], metadata: Optional[dict],
    ) -> SendResult:
        wait = self._slot_remaining(chat_id)
        if wait > 0:  # a send WAITS for its slot (a skipped send would drop the message)
            await asyncio.sleep(wait)
        chunks = split_text(self.format_message(content), self.MAX_MESSAGE_LENGTH)
        delivered: list = []
        target = self._target(chat_id)
        reply_id = self._reply_anchor(chat_id, reply_to)
        thread_id = self._thread_id(metadata)
        for chunk in chunks:
            self._hold_slot(chat_id)
            try:
                try:
                    result = await self._client.send_text(
                        target, chunk, thread_id=thread_id,
                        reply_message_id=reply_id if (not delivered or self._reply_mode == "all") else None)
                except (YMNotFound, YMBadRequest) as exc:
                    if reply_id is None:
                        raise
                    logger.debug("[%s] reply anchor rejected (%s); resending without it", self.name, exc)
                    reply_id = None
                    result = await self._client.send_text(target, chunk, thread_id=thread_id)
            except YMApiError as exc:
                failure = self._error_result(exc)
                if delivered:  # earlier chunks are visible: report the partial_overflow contract
                    failure.raw_response = {
                        "partial_overflow": True, "delivered_chunks": len(delivered), "total_chunks": len(chunks),
                        "last_message_id": delivered[-1], "continuation_message_ids": tuple(delivered[1:]),
                    }
                logger.error("[%s] send failed: %s", self.name, exc)
                return failure
            delivered.append(str(result.get("message_id")))
        return SendResult(
            success=True, message_id=delivered[0] if delivered else None,
            raw_response={"message_ids": list(delivered)},
        )

    def format_message(self, content: str) -> str:
        return to_yandex_markdown(content)

    async def edit_message(
        self, chat_id: str, message_id: str, content: str, *, finalize: bool = False,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Streaming edit (``sendText`` with ``message_id``).

        Interim edits are skipped while the per-chat slot is busy (the next edit shows the newer
        text anyway); a finalize edit is never gated. Oversized interim text is a truncated preview
        (deduplicated); oversized final text edits the first chunk and sends the rest.
        """
        if not self._client:
            return SendResult(success=False, error="Not connected")
        if not finalize and self._slot_remaining(chat_id) > 0:
            return SendResult(success=True, message_id=message_id, raw_response={"skipped": True})
        formatted = self.format_message(content)
        key = (str(chat_id), str(message_id))
        if finalize:
            self._last_overflow_preview.pop(key, None)
        saturated = False
        if len(formatted) > self.MAX_MESSAGE_LENGTH:
            if finalize:
                return await self._edit_overflow_split(chat_id, message_id, formatted, metadata)
            formatted = split_text(formatted, self.MAX_MESSAGE_LENGTH)[0]
            saturated = True
            if self._last_overflow_preview.get(key) == formatted:
                return SendResult(success=True, message_id=message_id)
        elif not finalize:
            self._last_overflow_preview.pop(key, None)
        self._hold_slot(chat_id)
        try:
            await self._edit_with_flood_retry(chat_id, message_id, formatted, finalize=finalize)
        except YMApiError as exc:
            if "not modified" in str(exc).lower() or "same" in exc.description.lower():
                return SendResult(success=True, message_id=message_id)
            logger.warning("[%s] edit failed: %s", self.name, exc)
            return self._error_result(exc)
        if saturated:
            self._last_overflow_preview[key] = formatted
        return SendResult(success=True, message_id=message_id)

    async def _edit_with_flood_retry(self, chat_id: str, message_id: str, text: str, *, finalize: bool) -> None:
        try:
            await self._client.edit_text(self._target(chat_id), int(message_id), text)
        except YMRateLimited as exc:
            wait = exc.retry_after or 1.0
            if not finalize or wait > _FLOOD_INLINE_WAIT_CAP_SECS:
                raise
            await asyncio.sleep(wait)
            await self._client.edit_text(self._target(chat_id), int(message_id), text)

    async def _edit_overflow_split(
        self, chat_id: str, message_id: str, formatted: str, metadata: Optional[dict],
    ) -> SendResult:
        chunks = split_text(formatted, self.MAX_MESSAGE_LENGTH)
        target = self._target(chat_id)
        thread_id = self._thread_id(metadata)
        try:
            await self._edit_with_flood_retry(chat_id, message_id, chunks[0], finalize=True)
        except YMApiError as exc:
            return self._error_result(exc)
        continuation: list = []
        prev = message_id
        for chunk in chunks[1:]:
            self._hold_slot(chat_id)
            try:
                result = await self._client.send_text(target, chunk, thread_id=thread_id)
            except YMApiError as exc:
                logger.warning("[%s] overflow continuation failed: %s", self.name, exc)
                failure = self._error_result(exc)
                failure.error = "overflow_continuation_failed"
                failure.retryable = True
                failure.message_id = prev
                failure.continuation_message_ids = tuple(continuation)
                failure.raw_response = {
                    "partial_overflow": True, "delivered_chunks": 1 + len(continuation), "total_chunks": len(chunks),
                    "last_message_id": prev, "continuation_message_ids": tuple(continuation),
                }
                return failure
            prev = str(result.get("message_id"))
            continuation.append(prev)
        return SendResult(
            success=True, message_id=continuation[-1] if continuation else message_id,
            continuation_message_ids=tuple(continuation),
        )

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if not self._client:
            return False
        try:
            await self._client.delete_message(self._target(chat_id), int(message_id))
            return True
        except (YMApiError, ValueError):
            return False

    async def send_private_notice(
        self, chat_id: str, user_id: Optional[str], content: str, reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Deliver to ``user_id`` (a login) in their private chat; groups are never used for notices."""
        if not self._client or not content:
            return SendResult(success=False, error="Not connected")
        if not user_id:
            return await self.send(chat_id, content, reply_to=reply_to, metadata=metadata) \
                if self._is_private(chat_id) else SendResult(success=False, error="no recipient")
        try:
            result = await self._client.send_text(Target.for_login(user_id), self.format_message(content)[:6000])
            return SendResult(success=True, message_id=str(result.get("message_id")))
        except YMApiError as exc:
            if self._is_private(chat_id):  # DM by login refused: the chat itself is private, use it
                return await self.send(chat_id, content, reply_to=None, metadata=metadata)
            return self._error_result(exc)

    # ------------------------------------------------------------------ typing / status
    async def send_typing(self, chat_id: str, metadata: Optional[dict] = None) -> None:
        if not self._client or not chat_id:
            return
        until = self._typing_cooldown.get(str(chat_id))
        if until is not None and until > _loop_time():
            return
        status = self._status_text.get(str(chat_id))
        try:
            if status and self._is_private(chat_id):
                await self._client.send_typing(
                    self._target(chat_id), kind="processing", timeout=_TYPING_SECONDS,
                    processing_text=status[:100], thread_id=self._thread_id(metadata))
            else:
                await self._client.send_typing(
                    self._target(chat_id), kind="text", timeout=_TYPING_SECONDS, thread_id=self._thread_id(metadata))
        except YMRateLimited as exc:
            self._typing_cooldown[str(chat_id)] = _loop_time() + (exc.retry_after or 5.0)
        except YMApiError as exc:
            logger.debug("[%s] typing indicator failed: %s", self.name, exc)

    async def stop_typing(self, chat_id: str) -> None:
        """The API has no explicit stop: the indicator expires after its timeout."""

    # ------------------------------------------------------------------ outbound: media
    @staticmethod
    def _missing(path: str) -> SendResult:
        return SendResult(success=False, error=f"file not found: {path}")

    async def _caption(self, chat_id: str, caption: Optional[str], metadata: Optional[dict]) -> None:
        if caption and caption.strip():
            with contextlib.suppress(Exception):
                await self.send(chat_id, caption, metadata=metadata)

    async def _send_media_file(
        self, kind: str, chat_id: str, path: str, caption: Optional[str], reply_to: Optional[str],
        metadata: Optional[dict], file_name: Optional[str] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Not connected")
        if not os.path.isfile(path):
            return self._missing(path)
        sender = self._client.send_image if kind == "image" else self._client.send_file
        try:
            result = await sender(
                self._target(chat_id), path, file_name or display_filename(path),
                reply_message_id=self._reply_anchor(chat_id, reply_to), thread_id=self._thread_id(metadata),
            )
        except YMApiError as exc:
            logger.error("[%s] sending %s failed: %s", self.name, kind, exc)
            return self._error_result(exc)
        await self._caption(chat_id, caption, metadata)
        return SendResult(success=True, message_id=str(result.get("message_id")), raw_response=result)

    async def send_image_file(
        self, chat_id: str, image_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None,
        metadata: Optional[dict] = None, **kwargs: Any,
    ) -> SendResult:
        return await self._send_media_file("image", chat_id, image_path, caption, reply_to, metadata)

    async def send_image(
        self, chat_id: str, image_url: str, caption: Optional[str] = None, reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """URL -> fetched through Hermes' guarded downloader -> uploaded; text fallback on failure."""
        try:
            ext = os.path.splitext(image_url.split("?")[0])[1].lower() or ".jpg"
            path = await cache_image_from_url(image_url, ext=ext)
            return await self.send_image_file(chat_id, path, caption, reply_to, metadata)
        except Exception as exc:
            logger.warning("[%s] image URL send failed, falling back to text: %s", self.name, exc)
            text = f"{caption}\n{image_url}" if caption else image_url
            return await self.send(chat_id, text, reply_to=reply_to, metadata=metadata)

    async def send_multiple_images(
        self, chat_id: str, images: list, metadata: Optional[dict] = None, human_delay: float = 0.0,
    ) -> SendResult:
        """Albums of up to 10 through ``sendGallery``; animated GIFs and failures go one by one."""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        if not images:
            return SendResult(success=False, error="no images to send")
        paths: list = []
        alts: list = []
        fallback: list = []
        for url, alt in images:
            try:
                if url.startswith("file://"):
                    from urllib.request import url2pathname

                    path = url2pathname(url[7:])
                elif self._is_animation_url(url):
                    fallback.append((url, alt))
                    continue
                else:
                    ext = os.path.splitext(url.split("?")[0])[1].lower() or ".jpg"
                    path = await cache_image_from_url(url, ext=ext)
                if os.path.isfile(path):
                    paths.append(path)
                    alts.append(alt or "")
            except Exception as exc:
                logger.warning("[%s] preparing image %s failed: %s", self.name, url[:80], exc)
                fallback.append((url, alt))
        delivered = False
        for start in range(0, len(paths), 10):
            batch, batch_alts = paths[start:start + 10], alts[start:start + 10]
            caption = "\n".join(a for a in batch_alts if a)[:6000] or None
            try:
                await self._client.send_gallery(
                    self._target(chat_id), batch, text=caption, thread_id=self._thread_id(metadata))
                delivered = True
            except YMApiError as exc:
                logger.warning("[%s] gallery failed (%s); sending one by one", self.name, exc)
                fallback.extend((f"file://{p}", a) for p, a in zip(batch, batch_alts))
            if human_delay > 0:
                await asyncio.sleep(human_delay)
        if fallback:
            result = await super().send_multiple_images(chat_id, fallback, metadata, human_delay)
            delivered = delivered or result.success
        return SendResult(success=delivered, error=None if delivered else "all images failed to send")

    async def send_document(
        self, chat_id: str, file_path: str, caption: Optional[str] = None, file_name: Optional[str] = None,
        reply_to: Optional[str] = None, metadata: Optional[dict] = None, **kwargs: Any,
    ) -> SendResult:
        return await self._send_media_file("file", chat_id, file_path, caption, reply_to, metadata, file_name)

    async def send_voice(
        self, chat_id: str, audio_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None,
        metadata: Optional[dict] = None, **kwargs: Any,
    ) -> SendResult:
        return await self._send_media_file("file", chat_id, audio_path, caption, reply_to, metadata)

    async def send_video(
        self, chat_id: str, video_path: str, caption: Optional[str] = None, reply_to: Optional[str] = None,
        metadata: Optional[dict] = None, **kwargs: Any,
    ) -> SendResult:
        return await self._send_media_file("file", chat_id, video_path, caption, reply_to, metadata)

    # ------------------------------------------------------------------ chat info
    async def get_chat_info(self, chat_id: str) -> dict:
        cached = self._chat_info_cache.get(str(chat_id))
        if cached:
            return cached
        fallback = {
            "name": str(chat_id), "type": "dm" if self._is_private(chat_id) else "group", "chat_id": str(chat_id),
        }
        if not self._client:
            return fallback
        try:
            info = await self._client.get_chat(str(chat_id))
        except YMApiError as exc:
            logger.debug("[%s] get_chat failed: %s", self.name, exc)
            return fallback
        kind = {"private": "dm", "group": "group", "channel": "channel"}.get(str(info.get("type")), fallback["type"])
        result = {
            "name": info.get("name") or info.get("title") or str(chat_id), "type": kind,
            "chat_id": str(info.get("id") or chat_id),
        }
        self._chat_info_cache[str(chat_id)] = result
        return result

    # ------------------------------------------------------------------ reactions
    def _reactions_enabled(self) -> bool:
        return self._settings.reactions and not self._reactions_broken and self._client is not None

    async def _available_reactions(self, chat_id: str) -> Optional[frozenset]:
        known = self._reaction_names.get(str(chat_id))
        if known is not None:
            return known
        try:
            info = await self._client.get_chat(str(chat_id))
        except YMApiError as exc:
            logger.debug("[%s] reactions lookup failed: %s", self.name, exc)
            return None
        names = frozenset(
            str(r.get("name")) for r in (info.get("available_reactions") or []) if isinstance(r, dict) and r.get("name")
        )
        self._reaction_names[str(chat_id)] = names
        return names

    async def _add_reaction(self, chat_id: str, message_id: str, emoji: str) -> bool:
        """``emoji`` is the Yandex reaction NAME; it is only used when the chat offers it."""
        if not self._reactions_enabled() or not emoji or not str(message_id).isdigit():
            return False
        try:
            names = await self._available_reactions(chat_id)
            if names is not None and emoji not in names:
                return False
            await self._client.send_reaction(self._target(chat_id), int(message_id), emoji)
            return True
        except YMBadRequest as exc:
            logger.warning("[%s] reaction %r rejected (%s); disabling reactions for this run", self.name, emoji, exc)
            self._reactions_broken = True
        except YMApiError as exc:
            logger.debug("[%s] reaction failed: %s", self.name, exc)
        return False

    async def _remove_reaction(self, chat_id: str, message_id: str) -> bool:
        if not self._reactions_enabled() or not str(message_id).isdigit():
            return False
        try:
            await self._client.send_reaction(self._target(chat_id), int(message_id), None)
            return True
        except YMApiError as exc:
            logger.debug("[%s] reaction removal failed: %s", self.name, exc)
            return False

    async def on_processing_start(self, event: MessageEvent) -> None:
        ack = self._settings.reaction_ack
        chat_id = getattr(event.source, "chat_id", None)
        if ack and chat_id and event.message_id:
            await self._add_reaction(chat_id, event.message_id, ack)


# --------------------------------------------------------------------------- registration helpers
def _env_credentials_present() -> bool:
    return bool(str(get_scoped_secret("YANDEX_MESSENGER_BOT_TOKEN") or "").strip())


def check_requirements() -> bool:
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config) -> bool:
    return bool(load_settings(config).token)


def is_connected(config) -> bool:
    return validate_config(config)


_SETUP_PROMPTS = (
    ("YANDEX_MESSENGER_BOT_TOKEN", "OAuth-токен бота", True),
    ("YANDEX_MESSENGER_ALLOWED_USERS", "Allowed users (логины через запятую; пусто = пропустить)", False),
    ("YANDEX_MESSENGER_HOME_CHANNEL", "Home channel для cron (chat_id, login или chat:<id>; пусто = пропустить)", False),
)


def interactive_setup() -> None:
    from hermes_cli.cli_output import print_header, print_info, prompt
    from hermes_cli.config import get_env_value, save_env_value
    from hermes_cli.setup_platforms import declines_reconfigure

    print_header("Yandex Messenger Bot API")
    if declines_reconfigure("Yandex Messenger", "Reconfigure Yandex Messenger?", "YANDEX_MESSENGER_BOT_TOKEN"):
        return
    print_info("Create a bot at https://admin.yandex.ru/bot-platform and copy its OAuth token.")
    for var, question, secret in _SETUP_PROMPTS:
        suffix = " [keep current]" if get_env_value(var) else ""
        value = prompt(f"{question}{suffix}", password=secret)
        if value:
            save_env_value(var, value)
    print_info("Polling is used by default; set YANDEX_MESSENGER_WEBHOOK_URL (+ _WEBHOOK_SECRET) for webhook mode.")


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    from .adapter_hint import PLATFORM_HINT

    ctx.register_platform(
        name=PLATFORM_NAME, label="Yandex Messenger",
        adapter_factory=lambda cfg: YandexMessengerAdapter(cfg), check_fn=check_requirements,
        validate_config=validate_config, is_connected=is_connected,
        required_env=["YANDEX_MESSENGER_BOT_TOKEN"], install_hint="aiohttp ships with Hermes (pip install aiohttp)",
        setup_fn=interactive_setup, cron_deliver_env_var="YANDEX_MESSENGER_HOME_CHANNEL",
        standalone_sender_fn=standalone_send, max_message_length=MAX_TEXT_LENGTH, emoji="💬",
        pii_safe=False, allow_update_command=True, platform_hint=PLATFORM_HINT,
        env_overrides=gateway_env.ENV_OVERRIDES, env_enable_credentials=gateway_env.ENV_ENABLE_CREDENTIALS,
        **gateway_env.AUTHZ,
    )
