"""Async client for the Yandex Messenger Bot API (aiohttp only)."""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import logging
import mimetypes
import os
import random
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence, Union

import aiohttp

from .errors import (
    YMApiError,
    YMFileTooLarge,
    YMTransportError,
    error_for,
)
from .types import Target

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://botapi.messenger.yandex.net/bot/v1"
CDN_FALLBACK_BASE = "https://storage.mds.yandex.net/"
MAX_TEXT_LENGTH = 6000

TargetLike = Union[Target, str]
FileSource = Union[str, os.PathLike, bytes, bytearray]


def _mask(token: str) -> str:
    return "***" if token else ""


def _to_target(target: TargetLike) -> Target:
    return Target.parse(target)


class YandexMessengerClient:
    """Thin, typed wrapper over the Bot API.

    * Errors raise ``YMApiError`` subclasses (``ok: false`` is an error too).
    * Connection errors / 5xx are retried (``max_attempts``) with backoff + jitter;
      429 is NEVER slept on here: ``YMRateLimited.retry_after`` is raised for the caller.
    * The token is only ever sent in the ``Authorization`` header and never logged.
    """

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[aiohttp.ClientSession] = None,
        timeout: float = 60.0,
        *,
        max_attempts: int = 2,
        backoff_base: float = 0.5,
        trust_env: bool = False,
        proxy: Optional[str] = None,
    ) -> None:
        """``proxy``: ``http://[user:pass@]host:port``, ``https://...`` or ``socks5://`` / ``socks5h://``
        (SOCKS needs the optional ``aiohttp_socks`` package; remote DNS is always used). It takes
        precedence over ``trust_env`` (which only reads ``HTTP(S)_PROXY`` / ``ALL_PROXY`` from the env).
        An injected ``session`` is used as is: configure its proxy yourself."""
        self._token = token or ""
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._timeout = timeout
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = backoff_base
        self._trust_env = trust_env
        self._proxy = (proxy or "").strip() or None

    # ------------------------------------------------------------------ lifecycle
    def __repr__(self) -> str:  # never leak the token or proxy credentials
        proxy = "set" if self._proxy else None
        return (
            f"YandexMessengerClient(base_url={self.base_url!r}, token={_mask(self._token)!r}, proxy={proxy!r})"
        )

    @property
    def _is_socks(self) -> bool:
        return bool(self._proxy) and self._proxy.lower().startswith("socks")

    def _proxy_kwargs(self) -> dict:
        """Per-request kwargs: an HTTP(S) proxy is passed per request, SOCKS goes through the connector."""
        if self._proxy and not self._is_socks and self._owns_session:
            return {"proxy": self._proxy}
        return {}

    def _build_connector(self) -> Any:
        if not self._is_socks:
            return None
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as exc:
            raise YMTransportError(
                "a SOCKS proxy is configured but the 'aiohttp_socks' package is not installed "
                "(pip install aiohttp-socks) - or use an HTTP proxy",
                method="proxy", kind="transient",
            ) from exc
        return ProxyConnector.from_url(self._proxy, rdns=True)

    async def __aenter__(self) -> "YandexMessengerClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = self._build_connector()
            extra = {"connector": connector} if connector is not None else {}
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout), trust_env=self._trust_env, **extra
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ transport
    @property
    def _auth(self) -> dict:
        return {"Authorization": f"OAuth {self._token}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _retry_after(resp: aiohttp.ClientResponse) -> Optional[float]:
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    async def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        await asyncio.sleep(delay + random.uniform(0, delay / 2))

    async def _request(
        self,
        http_method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        form_factory: Any = None,
        timeout: Optional[float] = None,
        label: Optional[str] = None,
    ) -> dict:
        """Perform one API call and return the decoded JSON body (``ok`` is verified)."""
        method_label = label or path.strip("/")
        session = await self._ensure_session()
        client_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else None
        last_exc: Optional[YMApiError] = None
        for attempt in range(1, self._max_attempts + 1):
            kwargs: dict = {"headers": self._auth, **self._proxy_kwargs()}
            if json is not None:
                kwargs["json"] = json
            if params:
                kwargs["params"] = params
            if form_factory is not None:
                kwargs["data"] = form_factory()
            if client_timeout is not None:
                kwargs["timeout"] = client_timeout
            try:
                async with session.request(http_method, self._url(path), **kwargs) as resp:
                    body = await resp.read()
                    status = resp.status
                    retry_after = self._retry_after(resp)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_exc = YMTransportError(
                    f"{type(exc).__name__}: {exc}", method=method_label, kind="transient"
                )
                if attempt < self._max_attempts:
                    await self._sleep_backoff(attempt)
                    continue
                raise last_exc from exc
            data = self._decode(body)
            if status >= 500 and attempt < self._max_attempts:
                last_exc = error_for(status, str(data.get("description", "")), method=method_label)
                await self._sleep_backoff(attempt)
                continue
            if status >= 400 or data.get("ok") is False:
                raise error_for(
                    status if status >= 400 else 400,
                    str(data.get("description") or f"HTTP {status}"),
                    method=method_label,
                    retry_after=retry_after,
                )
            return data
        assert last_exc is not None  # pragma: no cover
        raise last_exc

    @staticmethod
    def _decode(body: bytes) -> dict:
        if not body:
            return {}
        try:
            data = _json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ bot
    async def get_self(self) -> dict:
        return await self._request("GET", "self/get", label="self/get")

    async def update_self(self, webhook_url: Any = ..., settings: Optional[dict] = None) -> dict:
        """``webhook_url=None`` resets the webhook (JSON ``null``); omit it to leave unchanged."""
        payload: dict = {}
        if webhook_url is not ...:
            payload["webhook_url"] = webhook_url
        if settings:
            payload["settings"] = settings
        return await self._request("POST", "self/update/", json=payload, label="self/update")

    # ------------------------------------------------------------------ updates
    async def get_updates(self, offset: Optional[int] = None, limit: int = 100) -> list:
        payload: dict = {"limit": max(1, min(int(limit), 1000))}
        if offset:
            payload["offset"] = int(offset)
        data = await self._request("POST", "messages/getUpdates/", json=payload, label="getUpdates")
        updates = data.get("updates")
        return updates if isinstance(updates, list) else []

    # ------------------------------------------------------------------ text
    async def send_text(
        self,
        target: TargetLike,
        text: str,
        *,
        reply_message_id: Optional[int] = None,
        reply_quote: Optional[str] = None,
        thread_id: Optional[int] = None,
        suggest_buttons: Optional[dict] = None,
        disable_notification: Optional[bool] = None,
        important: Optional[bool] = None,
        disable_web_page_preview: Optional[bool] = None,
        payload_id: Optional[str] = None,
        message_id: Optional[int] = None,
        inline_keyboard: Optional[list] = None,
    ) -> dict:
        """Send (or, with ``message_id``, edit) a text message. Returns ``{"ok", "message_id"}``.

        ``inline_keyboard`` is the deprecated button format, kept only for legacy callers.

        A ``payload_id`` is generated for fresh sends so a retry after a lost response is
        de-duplicated server-side (identical ids are treated as duplicates).
        """
        payload: dict = {**_to_target(target).as_params(), "text": text}
        if message_id is not None:
            payload["message_id"] = int(message_id)
        else:
            payload["payload_id"] = payload_id or uuid.uuid4().hex
        if reply_message_id is not None:
            payload["reply_message_id"] = int(reply_message_id)
            if reply_quote:
                payload["reply_quote"] = reply_quote
        if thread_id is not None:
            payload["thread_id"] = int(thread_id)
        if suggest_buttons is not None:
            payload["suggest_buttons"] = suggest_buttons
        if inline_keyboard:
            payload["inline_keyboard"] = inline_keyboard
        for key, value in (
            ("disable_notification", disable_notification),
            ("important", important),
            ("disable_web_page_preview", disable_web_page_preview),
        ):
            if value is not None:
                payload[key] = bool(value)
        return await self._request("POST", "messages/sendText/", json=payload, label="sendText")

    async def edit_text(
        self,
        target: TargetLike,
        message_id: int,
        text: str,
        *,
        suggest_buttons: Optional[dict] = None,
        thread_id: Optional[int] = None,
    ) -> dict:
        return await self.send_text(
            target, text, message_id=message_id, suggest_buttons=suggest_buttons, thread_id=thread_id
        )

    async def delete_message(
        self, target: TargetLike, message_id: int, *, thread_id: Optional[int] = None
    ) -> dict:
        payload: dict = {**_to_target(target).as_params(), "message_id": int(message_id)}
        if thread_id is not None:
            payload["thread_id"] = int(thread_id)
        return await self._request("POST", "messages/delete/", json=payload, label="delete")

    async def send_typing(
        self,
        target: TargetLike,
        *,
        kind: str = "text",
        timeout: int = 3,
        processing_text: Optional[str] = None,
        thread_id: Optional[int] = None,
    ) -> dict:
        """``kind="processing"`` (private chats only) can show ``processing_text`` (<=100 chars)."""
        payload: dict = {
            **_to_target(target).as_params(),
            "type": kind,
            "timeout": max(1, min(int(timeout), 60)),
        }
        if kind == "processing":
            if processing_text:
                payload["processing_content"] = {"display": "text", "text": processing_text[:100]}
            else:
                payload["processing_content"] = {"display": "default"}
        if thread_id is not None:
            payload["thread_id"] = int(thread_id)
        return await self._request("POST", "messages/sendTyping/", json=payload, label="sendTyping")

    # ------------------------------------------------------------------ files
    @staticmethod
    def _load(source: FileSource, filename: Optional[str]) -> tuple:
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), filename or "file.bin"
        path = Path(os.fspath(source))
        return path.read_bytes(), filename or path.name

    @staticmethod
    def _content_type(filename: str) -> str:
        return mimetypes.guess_type(filename)[0] or "application/octet-stream"

    def _multipart(self, fields: dict, files: Sequence[tuple]) -> Any:
        """Return a factory producing a fresh ``FormData`` (a form cannot be re-sent on retry)."""

        def build() -> aiohttp.FormData:
            form = aiohttp.FormData()
            for key, value in fields.items():
                form.add_field(key, str(value) if not isinstance(value, str) else value)
            for field, data, filename in files:
                form.add_field(field, data, filename=filename, content_type=self._content_type(filename))
            return form

        return build

    def _media_fields(
        self,
        target: TargetLike,
        *,
        reply_message_id: Optional[int],
        thread_id: Optional[int],
        suggest_buttons: Optional[dict],
        disable_notification: Optional[bool],
        important: Optional[bool],
        message_id: Optional[int] = None,
        text: Optional[str] = None,
    ) -> dict:
        fields: dict = dict(_to_target(target).as_params())
        if text:
            fields["text"] = text
        if message_id is not None:
            fields["message_id"] = int(message_id)
        if reply_message_id is not None:
            fields["reply_message_id"] = int(reply_message_id)
        if thread_id is not None:
            fields["thread_id"] = int(thread_id)
        if suggest_buttons is not None:
            fields["suggest_buttons"] = _json.dumps(suggest_buttons, ensure_ascii=False)
        if disable_notification is not None:
            fields["disable_notification"] = "true" if disable_notification else "false"
        if important is not None:
            fields["important"] = "true" if important else "false"
        return fields

    async def send_file(
        self,
        target: TargetLike,
        source: FileSource,
        filename: Optional[str] = None,
        *,
        reply_message_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        suggest_buttons: Optional[dict] = None,
        disable_notification: Optional[bool] = None,
        important: Optional[bool] = None,
    ) -> dict:
        data, name = self._load(source, filename)
        fields = self._media_fields(
            target,
            reply_message_id=reply_message_id,
            thread_id=thread_id,
            suggest_buttons=suggest_buttons,
            disable_notification=disable_notification,
            important=important,
        )
        return await self._request(
            "POST",
            "messages/sendFile/",
            form_factory=self._multipart(fields, [("document", data, name)]),
            timeout=max(self._timeout, 300.0),
            label="sendFile",
        )

    async def send_image(
        self,
        target: TargetLike,
        source: FileSource,
        filename: Optional[str] = None,
        *,
        reply_message_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        suggest_buttons: Optional[dict] = None,
        disable_notification: Optional[bool] = None,
        important: Optional[bool] = None,
    ) -> dict:
        data, name = self._load(source, filename)
        fields = self._media_fields(
            target,
            reply_message_id=reply_message_id,
            thread_id=thread_id,
            suggest_buttons=suggest_buttons,
            disable_notification=disable_notification,
            important=important,
        )
        return await self._request(
            "POST",
            "messages/sendImage/",
            form_factory=self._multipart(fields, [("image", data, name)]),
            timeout=max(self._timeout, 300.0),
            label="sendImage",
        )

    async def send_gallery(
        self,
        target: TargetLike,
        images: Sequence[Union[FileSource, tuple]],
        *,
        text: Optional[str] = None,
        reply_message_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        suggest_buttons: Optional[dict] = None,
        disable_notification: Optional[bool] = None,
        important: Optional[bool] = None,
    ) -> dict:
        """``images`` items: a path/bytes, or a ``(source, filename)`` tuple. Up to 10 per album."""
        files = []
        for index, item in enumerate(images):
            source, name = item if isinstance(item, tuple) else (item, None)
            if isinstance(source, (bytes, bytearray)) and not name:
                name = f"image{index}.jpg"
            data, resolved = self._load(source, name)
            files.append(("images", data, resolved))
        if not files:
            raise ValueError("send_gallery needs at least one image")
        fields = self._media_fields(
            target,
            reply_message_id=reply_message_id,
            thread_id=thread_id,
            suggest_buttons=suggest_buttons,
            disable_notification=disable_notification,
            important=important,
            text=text,
        )
        return await self._request(
            "POST",
            "messages/sendGallery/",
            form_factory=self._multipart(fields, files),
            timeout=max(self._timeout, 300.0),
            label="sendGallery",
        )

    @contextlib.asynccontextmanager
    async def _file_stream(self, file_id: str) -> AsyncIterator[aiohttp.ClientResponse]:
        """Yield a response whose body is the raw file.

        ``getFile`` normally streams the bytes; some deployments answer with JSON holding a
        CDN ``file_path`` instead, which is followed transparently.
        """
        session = await self._ensure_session()
        headers = {**self._auth, "Content-Type": "application/json"}
        url = self._url("messages/getFile/")
        timeout = aiohttp.ClientTimeout(total=3600, sock_read=600, connect=60)
        try:
            async with session.post(
                url, headers=headers, json={"file_id": file_id}, timeout=timeout, **self._proxy_kwargs()
            ) as resp:
                if resp.status >= 400:
                    data = self._decode(await resp.read())
                    raise error_for(resp.status, str(data.get("description") or f"HTTP {resp.status}"), method="getFile", retry_after=self._retry_after(resp))
                if "application/json" in resp.headers.get("Content-Type", ""):
                    data = self._decode(await resp.read())
                    if data.get("ok") is False:
                        raise error_for(400, str(data.get("description") or "getFile failed"), method="getFile")
                    cdn = data.get("file_path")
                    if not cdn:
                        raise error_for(404, "getFile returned no file content", method="getFile")
                    cdn_url = cdn if str(cdn).startswith("http") else CDN_FALLBACK_BASE + str(cdn).lstrip("/")
                    async with session.get(cdn_url, timeout=timeout, **self._proxy_kwargs()) as cdn_resp:
                        if cdn_resp.status >= 400:
                            raise error_for(cdn_resp.status, f"CDN HTTP {cdn_resp.status}", method="getFile(cdn)")
                        yield cdn_resp
                    return
                yield resp
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise YMTransportError(f"{type(exc).__name__}: {exc}", method="getFile", kind="transient") from exc

    @staticmethod
    def _check_declared_size(resp: aiohttp.ClientResponse, max_bytes: Optional[int]) -> None:
        if max_bytes and resp.content_length and resp.content_length > max_bytes:
            raise YMFileTooLarge(
                f"file is {resp.content_length} bytes, limit {max_bytes}", method="getFile", kind="too_long"
            )

    async def get_file(self, file_id: str, *, max_bytes: Optional[int] = None) -> bytes:
        async with self._file_stream(file_id) as resp:
            self._check_declared_size(resp, max_bytes)
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                buf.extend(chunk)
                if max_bytes and len(buf) > max_bytes:
                    raise YMFileTooLarge(f"file exceeds {max_bytes} bytes", method="getFile", kind="too_long")
            return bytes(buf)

    async def download_file(self, file_id: str, dest: Union[str, os.PathLike], *, max_bytes: Optional[int] = None) -> Path:
        """Stream a file to ``dest`` (partial files are removed on failure)."""
        path = Path(os.fspath(dest))
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            async with self._file_stream(file_id) as resp:
                self._check_declared_size(resp, max_bytes)
                with open(path, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(1 << 20):
                        written += len(chunk)
                        if max_bytes and written > max_bytes:
                            raise YMFileTooLarge(f"file exceeds {max_bytes} bytes", method="getFile", kind="too_long")
                        fh.write(chunk)
        except BaseException:
            with contextlib.suppress(OSError):
                path.unlink()
            raise
        return path

    # ------------------------------------------------------------------ chats / reactions
    async def send_reaction(
        self,
        target: TargetLike,
        message_id: int,
        name: Optional[str],
        *,
        thread_id: Optional[int] = None,
    ) -> dict:
        """Set reaction ``name`` (a ``default_reaction``) or remove ours when ``name`` is falsy."""
        payload: dict = {**_to_target(target).as_params(), "message_id": int(message_id)}
        if name:
            payload["reaction"] = {"type": "default_reaction", "name": name}
        else:
            payload["reaction"] = None
        if thread_id is not None:
            payload["thread_id"] = int(thread_id)
        return await self._request("POST", "messages/sendReaction/", json=payload, label="sendReaction")

    async def get_chat(self, chat_id: str) -> dict:
        data = await self._request("GET", "chats/getChat/", params={"chat_id": chat_id}, label="getChat")
        info = data.get("data")
        return info if isinstance(info, dict) else {}
