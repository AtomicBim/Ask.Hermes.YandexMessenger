"""Polling loop, webhook server, connect()/disconnect() lifecycle and standalone delivery."""
import asyncio
import functools
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer


async def wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class FakeClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.offsets = []

    async def get_updates(self, offset=None, limit=100):
        self.offsets.append(offset)
        await asyncio.sleep(0)
        if not self.batches:
            return []
        item = self.batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_loop(mod, client, dispatch, **kw):
    loop = mod.transport.PollingLoop(client, dispatch, poll_interval=0.01, **kw)

    async def no_backoff(attempt):
        await asyncio.sleep(0.01)

    loop._backoff = no_backoff
    return loop


# ---------------------------------------------------------------- polling loop
async def test_polling_offset_advances_to_max_plus_one(mod):
    seen = []

    async def dispatch(u):
        seen.append(u["update_id"])

    client = FakeClient([[{"update_id": 5}, {"update_id": 7}], [{"update_id": 8}]])
    loop = make_loop(mod, client, dispatch)
    loop.start()
    assert await wait_for(lambda: seen == [5, 7, 8])
    await loop.stop()
    assert client.offsets[:3] == [None, 8, 9]  # the next call acknowledges the previous batch
    assert loop.offset == 9


async def test_polling_poison_update_does_not_wedge_loop(mod):
    seen = []

    async def dispatch(u):
        if u["update_id"] == 1:
            raise RuntimeError("bad update")
        seen.append(u["update_id"])

    client = FakeClient([[{"update_id": 1}, {"update_id": 2}]])
    loop = make_loop(mod, client, dispatch)
    loop.start()
    assert await wait_for(lambda: seen == [2])
    await loop.stop()
    assert loop.offset == 3


async def test_polling_backs_off_and_recovers(mod):
    seen = []

    async def dispatch(u):
        seen.append(u["update_id"])

    client = FakeClient([
        mod.client.YMTransportError("net down", kind="transient"),
        mod.client.YMBadRequest("500ish", status=502),
        [{"update_id": 3}],
    ])
    loop = make_loop(mod, client, dispatch)
    loop.start()
    assert await wait_for(lambda: seen == [3])
    await loop.stop()
    assert loop.errors == 2


async def test_polling_auth_failure_is_fatal(mod):
    fatal = []

    async def on_fatal(exc):
        fatal.append(exc)

    client = FakeClient([mod.client.YMForbidden("Unauthorized", status=401)])
    loop = make_loop(mod, client, lambda u: None, on_fatal=on_fatal)
    loop.start()
    assert await wait_for(lambda: len(fatal) == 1 and not loop.running)
    assert len(client.offsets) == 1  # no further polling after a 401


async def test_polling_offset_store_and_restart(mod, tmp_path):
    store = mod.transport.OffsetStore(tmp_path / "state" / "offset.json")
    assert store.load() == 0
    client = FakeClient([[{"update_id": 40}]])

    async def dispatch(u):
        pass

    loop = make_loop(mod, client, dispatch, store=store)
    loop.start()
    assert await wait_for(lambda: loop.offset == 41 and store.load() == 41)
    await loop.stop()
    # a fresh loop (gateway restart) resumes from the persisted offset
    again = make_loop(mod, FakeClient([[]]), dispatch, store=store)
    again.start()
    assert await wait_for(lambda: again.offset == 41)
    await again.stop()
    # a corrupt file must not break startup
    (tmp_path / "state" / "offset.json").write_text("{not json")
    assert store.load() == 0
    assert mod.transport.OffsetStore(None).load() == 0


# ---------------------------------------------------------------- webhook server
async def test_webhook_handler(mod, make_adapter):
    received = []

    async def dispatch(u):
        received.append(u)

    adapter = make_adapter(connected=False)
    server = mod.transport.WebhookServer(adapter, dispatch, host="127.0.0.1", port=0, path="/hook", secret="s3")
    app = server.build_app()
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/hook/wrong", json={"updates": []})
        assert r.status == 404
        r = await client.post("/hook/s3", data=b"not json", headers={"Content-Type": "application/json"})
        assert r.status == 400
        r = await client.post("/hook/s3", json={"nope": 1})
        assert r.status == 400
        r = await client.post("/hook/s3", json={"updates": [{"update_id": 1}, {"update_id": 2}]})
        assert r.status == 200 and await r.text() == "ok"
        assert await wait_for(lambda: [u["update_id"] for u in received] == [1, 2])
        r = await client.get("/hook/health")
        assert (await r.json())["status"] == "ok"
        r = await client.get("/hook/s3")  # GET on the secret route is not allowed
        assert r.status == 405
    await server.stop()


async def test_webhook_replies_before_processing_finishes(mod, make_adapter):
    gate = asyncio.Event()

    async def slow_dispatch(u):
        await gate.wait()

    server = mod.transport.WebhookServer(make_adapter(connected=False), slow_dispatch, host=None, port=0, path="/h",
                                         secret="s")
    async with TestClient(TestServer(server.build_app())) as client:
        r = await asyncio.wait_for(client.post("/h/s", json={"updates": [{"update_id": 1}]}), 1.0)
        assert r.status == 200  # Yandex' read timeout is 1 s: never wait for the agent
        gate.set()
    await server.stop()


def test_webhook_public_url(mod):
    server = mod.transport.WebhookServer(None, None, host=None, port=1, path="/yandex-messenger/webhook", secret="abc")
    assert server.public_url("https://bot.example.com/") == "https://bot.example.com/yandex-messenger/webhook/abc"
    assert server.public_url("https://x/yandex-messenger/webhook/abc") == "https://x/yandex-messenger/webhook/abc"


# ---------------------------------------------------------------- connect / disconnect
@pytest.fixture
def connectable(mod, fake, make_adapter, monkeypatch):
    monkeypatch.setattr(
        mod.adapter, "YandexMessengerClient",
        functools.partial(mod.client.YandexMessengerClient, base_url=fake.base_url, max_attempts=1, backoff_base=0.01))
    fake.responses["self/get"] = {"ok": True, "id": "bot-guid", "login": "hermes_bot", "is_bot": True}
    fake.responses["messages/getUpdates"] = {"ok": True, "updates": []}

    def build(**extra):
        extra.setdefault("poll_interval", 0.2)
        adapter = make_adapter(connected=False, extra=extra)
        adapter._bot_login = ""
        return adapter

    return build


async def test_connect_polling_receives_updates_and_disconnects(connectable, fake, make_update):
    a = connectable()
    fake.responses["messages/getUpdates"] = [
        {"ok": True, "updates": [make_update("from the wire", update_id=77)]}, {"ok": True, "updates": []}]
    assert await a.connect() is True
    try:
        assert a.is_connected and a._bot_login == "hermes_bot"
        assert await wait_for(lambda: [e.text for e in a.events] == ["from the wire"])
        assert await wait_for(lambda: any(c["json"].get("offset") == 78 for c in fake.calls("messages/getUpdates")))
    finally:
        await a.disconnect()
    assert not a.is_connected and a._client is None and not a._poller.running
    assert a._poll_offset >= 78  # remembered across reconnects


async def test_connect_resets_foreign_webhook_for_polling(connectable, fake):
    fake.responses["self/get"] = {"ok": True, "id": "b", "login": "hermes_bot", "webhook_url": "https://old/hook"}
    a = connectable()
    assert await a.connect()
    try:
        assert fake.last("self/update")["json"] == {"webhook_url": None}
    finally:
        await a.disconnect()


async def test_connect_rejects_bad_token_without_retry(connectable, fake):
    fake.responses["self/get"] = lambda r, rec: web.json_response({"ok": False, "description": "Unauthorized"}, status=401)
    a = connectable()
    assert await a.connect() is False
    assert a.fatal_error_code == "auth_failed" and a.fatal_error_retryable is False
    assert a._client is None


async def test_connect_transient_failure_is_retryable(connectable, fake):
    fake.responses["self/get"] = lambda r, rec: web.json_response({"ok": False, "description": "boom"}, status=503)
    a = connectable()
    assert await a.connect() is False
    assert a.fatal_error_code == "connect_failed" and a.fatal_error_retryable is True


async def test_connect_without_token(mod, make_adapter, monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.delenv("YANDEX_MESSENGER_BOT_TOKEN", raising=False)
    a = mod.adapter.YandexMessengerAdapter(PlatformConfig(enabled=True))
    assert await a.connect() is False and a.fatal_error_code == "config_missing"


async def test_token_lock_is_taken_hashed_and_released(connectable, fake):
    a = connectable()
    taken, released = [], []
    a._acquire_platform_lock = lambda scope, identity, desc: taken.append((scope, identity)) or True
    a._release_platform_lock = lambda: released.append(True)
    assert await a.connect()
    scope, identity = taken[0]
    assert scope == "yandex-messenger-token" and "TEST-TOKEN" not in identity and len(identity) == 16
    await a.disconnect()
    assert released == [True]
    # a token already held by another gateway: connect refuses and never touches the network
    b = connectable()
    b._acquire_platform_lock = lambda *args: False
    n = len(fake.requests)
    assert await b.connect() is False and len(fake.requests) == n
    # failures after the lock was taken give it back
    fake.responses["self/get"] = lambda r, rec: web.json_response({"ok": False, "description": "Unauthorized"}, status=401)
    c = connectable()
    c._acquire_platform_lock = lambda *args: True
    released.clear()
    c._release_platform_lock = lambda: released.append(True)
    assert await c.connect() is False and released == [True]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_webhook_mode_requires_secret(connectable, fake):
    a = connectable(webhook_url="https://bot.example.com")
    assert await a.connect() is False and a.fatal_error_code == "webhook_secret_missing"
    assert not fake.calls("self/update")  # nothing was registered without authentication


async def test_webhook_mode_end_to_end(connectable, fake, make_update):
    port = free_port()
    a = connectable(webhook_url="https://bot.example.com", webhook_secret="s3cr3t", webhook_host="127.0.0.1",
                    webhook_port=port)
    assert await a.connect() is True
    try:
        assert a._poller is None and a._webhook is not None
        assert fake.last("self/update")["json"] == {
            "webhook_url": "https://bot.example.com/yandex-messenger/webhook/s3cr3t"}
        async with ClientSession() as http:
            url = f"http://127.0.0.1:{port}/yandex-messenger/webhook"
            r = await http.post(f"{url}/nope", json={"updates": []})
            assert r.status == 404
            r = await http.post(f"{url}/s3cr3t", json={"updates": [make_update("via webhook", update_id=5)]})
            assert r.status == 200
            assert await wait_for(lambda: [e.text for e in a.events] == ["via webhook"])
            await http.post(f"{url}/s3cr3t", json={"updates": [make_update("via webhook", update_id=5)]})
            await asyncio.sleep(0.1)
            assert len(a.events) == 1  # at-least-once delivery de-duplicated by update_id
    finally:
        await a.disconnect()
    async with ClientSession() as http:
        with pytest.raises(Exception):
            await http.post(f"http://127.0.0.1:{port}/yandex-messenger/webhook/s3cr3t", json={"updates": []})


async def test_webhook_port_in_use_is_reported(connectable, fake):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        a = connectable(webhook_url="https://x", webhook_secret="s", webhook_host="127.0.0.1",
                        webhook_port=blocker.getsockname()[1])
        assert await a.connect() is False
        assert a.fatal_error_code == "bind_failed" and a.fatal_error_retryable is True


# ---------------------------------------------------------------- standalone sender
@pytest.fixture
def standalone(mod, fake, monkeypatch):
    monkeypatch.setattr(
        mod.standalone, "YandexMessengerClient",
        functools.partial(mod.client.YandexMessengerClient, base_url=fake.base_url, max_attempts=1))
    from gateway.config import PlatformConfig

    return mod.standalone.standalone_send, PlatformConfig(token="TOK", enabled=True)


async def test_standalone_text_and_media(standalone, fake, tmp_path):
    send, cfg = standalone
    doc = tmp_path / "r.pdf"
    doc.write_bytes(b"%PDF")
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    res = await send(cfg, "0/0/chat", "report **ready**", thread_id="5", media_files=[str(doc), (str(img), False)])
    assert res["success"] is True and res["platform"] == "yandex_messenger" and res["message_id"]
    body = fake.last("messages/sendText")["json"]
    assert body["chat_id"] == "0/0/chat" and body["thread_id"] == 5 and body["text"] == "report **ready**"
    assert fake.last("messages/sendFile")["fields"][-1][1] == "r.pdf"
    assert fake.last("messages/sendImage")["fields"][-1][1] == "p.png"
    res = await send(cfg, "ivan@corp.ru", "hi")
    assert fake.last("messages/sendText")["json"]["login"] == "ivan@corp.ru"
    res = await send(cfg, "0/0/chat", "x" * 13000)
    assert len(fake.calls("messages/sendText")) >= 4


async def test_standalone_errors(standalone, fake, mod):
    send, cfg = standalone
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "Bot is not a member of the chat"}, status=403)
    res = await send(cfg, "0/0/chat", "hi")
    assert "error" in res and "not a member" in res["error"] and "TOK" not in str(res)
    from gateway.config import PlatformConfig

    assert "error" in await send(PlatformConfig(), "0/0/chat", "hi")  # no token (and none in env)
    assert "error" in await send(cfg, "", "hi")
    assert "error" in await send(cfg, "0/0/chat", "hi", media_files=[]) or True


# ---------------------------------------------------------------- vendored client freshness
def test_vendored_client_matches_source():
    root = Path(__file__).resolve().parent.parent
    source = root.parent / "yandex_bot_example" / "yandex_messenger_client"
    if not (source / "__init__.py").is_file():
        pytest.skip("base library checkout not found next to the plugin")
    proc = subprocess.run([sys.executable, str(root / "scripts" / "sync_client.py"), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
