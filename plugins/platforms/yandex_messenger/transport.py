"""Inbound transports: ``getUpdates`` polling and the webhook HTTP listener."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ._vendor.yandex_messenger_client import YMApiError

logger = logging.getLogger(__name__)

Dispatch = Callable[[dict], Awaitable[None]]

MAX_BACKOFF_SECS = 60.0
WEBHOOK_BODY_MAX_BYTES = 2 * 1024 * 1024


class OffsetStore:
    """Persists the polling offset so a restart does not replay the last acknowledged batch."""

    def __init__(self, path: Optional[Path]) -> None:
        self._path = path

    def load(self) -> int:
        if self._path is None:
            return 0
        try:
            return int(json.loads(self._path.read_text(encoding="utf-8")).get("offset", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def save(self, offset: int) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": int(offset)}), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:  # persistence is best-effort
            logger.debug("Could not persist polling offset: %s", exc)


class PollingLoop:
    """Long-running ``getUpdates`` consumer.

    * Empty batch -> sleep ``poll_interval``; non-empty -> poll again immediately.
    * ``offset = max(update_id) + 1`` is advanced in ``finally`` for EVERY update, so a poison
      update can never wedge the loop (the next ``getUpdates`` call also acknowledges the batch).
    * Errors back off exponentially with jitter (cap 60 s). An authorization failure is fatal.
    * ``offset`` survives ``stop()``/``start()`` (reconnects).
    """

    def __init__(
        self,
        client: Any,
        dispatch: Dispatch,
        *,
        poll_interval: float = 1.0,
        limit: int = 100,
        offset: int = 0,
        store: Optional[OffsetStore] = None,
        on_fatal: Optional[Callable[[YMApiError], Awaitable[None]]] = None,
        on_batch: Optional[Callable[[], None]] = None,
    ) -> None:
        self._client = client
        self._dispatch = dispatch
        self.poll_interval = poll_interval
        self.limit = limit
        self.offset = offset
        self._store = store
        self._on_fatal = on_fatal
        self._on_batch = on_batch
        self._task: Optional[asyncio.Task] = None
        self.errors = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        if self._store is not None and not self.offset:
            self.offset = self._store.load()
        self._task = asyncio.create_task(self._run(), name="yandex-messenger-poll")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _backoff(self, attempt: int) -> None:
        delay = min(MAX_BACKOFF_SECS, 2.0 ** min(attempt, 6))
        await asyncio.sleep(delay + random.uniform(0, delay / 4))

    async def _run(self) -> None:
        attempt = 0
        while True:
            try:
                updates = await self._client.get_updates(offset=self.offset or None, limit=self.limit)
            except asyncio.CancelledError:
                raise
            except YMApiError as exc:
                if exc.kind == "forbidden" and exc.status in (401, 403):
                    logger.error("Yandex Messenger polling stopped: %s", exc)
                    if self._on_fatal is not None:
                        await self._on_fatal(exc)
                    return
                attempt += 1
                self.errors += 1
                logger.warning("Yandex Messenger getUpdates failed (retry #%d): %s", attempt, exc)
                await self._backoff(attempt)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                attempt += 1
                self.errors += 1
                logger.warning("Yandex Messenger polling error (retry #%d): %s", attempt, exc, exc_info=True)
                await self._backoff(attempt)
                continue
            attempt = 0
            if self._on_batch is not None:
                self._on_batch()
            if not updates:
                await asyncio.sleep(self.poll_interval)
                continue
            for update in updates:
                try:
                    await self._dispatch(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Yandex Messenger update dispatch failed")
                finally:
                    uid = update.get("update_id") if isinstance(update, dict) else None
                    if isinstance(uid, int):
                        self.offset = max(self.offset, uid + 1)
            if self._store is not None:
                self._store.save(self.offset)


class WebhookServer:
    """aiohttp listener answering ``200`` immediately (Yandex' read timeout is 1 s).

    Updates are processed in background tasks. The URL carries a secret segment
    (``{path}/{secret}``): the Bot API does not sign webhook calls, so the secret is the
    only authentication and webhook mode refuses to start without one.
    """

    def __init__(
        self,
        adapter: Any,
        dispatch: Dispatch,
        *,
        host: Optional[str],
        port: int,
        path: str,
        secret: str,
    ) -> None:
        self._adapter = adapter
        self._dispatch = dispatch
        self.host = host
        self.port = port
        self.path = path
        self.secret = secret
        self._runner: Any = None
        self._tasks: set = set()
        self.app: Any = None

    @property
    def route(self) -> str:
        return f"{self.path}/{{secret}}"

    def public_url(self, base: str) -> str:
        base = base.rstrip("/")
        full = f"{self.path}/{self.secret}"
        return base if base.endswith(full) else base + full

    def build_app(self) -> Any:
        from aiohttp import web

        app = web.Application(client_max_size=WEBHOOK_BODY_MAX_BYTES)
        app.router.add_get(f"{self.path}/health", self._health)
        app.router.add_post(self.route, self._handle)
        self.app = app
        return app

    async def start(self) -> None:
        from gateway.platforms.shared_ingress import bind_listener

        app = self.build_app()
        self._adapter._wire_plugin_handlers(app)
        self._runner = await bind_listener(
            self._adapter, app, self.host, self.port, self.path,
            reuse_address=False if sys.platform == "darwin" else None,
        )

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None

    async def _health(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"status": "ok", "platform": "yandex_messenger"})

    async def _handle(self, request: Any) -> Any:
        from aiohttp import web

        if not hmac.compare_digest(request.match_info.get("secret", ""), self.secret):
            return web.Response(status=404, text="not found")
        try:
            body = await request.read()
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return web.Response(status=400, text="bad request")
        updates = payload.get("updates") if isinstance(payload, dict) else None
        if not isinstance(updates, list):
            return web.Response(status=400, text="bad request")
        task = asyncio.create_task(self._process(updates))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.Response(status=200, text="ok")

    async def _process(self, updates: list) -> None:
        for update in updates:
            try:
                await self._dispatch(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Yandex Messenger webhook dispatch failed")
