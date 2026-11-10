"""Test harness: real Hermes ``gateway.*`` (from the WSL install) + a fake Yandex Bot API.

Run with ``scripts/run_tests_wsl.sh`` (it sets PYTHONPATH/HERMES_HOME). The plugin package is
imported the way Hermes imports user plugins: as a package loaded from its directory.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugins" / "platforms" / "yandex_messenger"
PKG = "yandex_messenger_plugin"

HERMES_REPO = os.environ.get("HERMES_REPO") or str(Path.home() / ".hermes" / "hermes-agent")
if HERMES_REPO not in sys.path and Path(HERMES_REPO).is_dir():
    sys.path.insert(0, HERMES_REPO)
sys.path.insert(0, str(Path(__file__).parent))

try:  # real gateway code is mandatory for the plugin tests
    import gateway.platforms.base  # noqa: F401
    HAVE_HERMES = True
except Exception:  # pragma: no cover
    HAVE_HERMES = False


def load_plugin():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(
        PKG, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


def pytest_collection_modifyitems(config, items):
    if HAVE_HERMES:
        return
    skip = pytest.mark.skip(reason="Hermes gateway.* not importable (run scripts/run_tests_wsl.sh)")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def plugin():
    load_plugin()
    return sys.modules[PKG]


@pytest.fixture
def mod(plugin):
    """Namespace giving access to plugin submodules (adapter, interactive, transport, ...)."""
    import importlib

    class Mods:
        pass

    ns = Mods()
    for name in ("adapter", "interactive", "transport", "settings", "standalone", "gateway_env", "adapter_hint"):
        setattr(ns, name, importlib.import_module(f"{PKG}.{name}"))
    ns.client = importlib.import_module(f"{PKG}._vendor.yandex_messenger_client")
    return ns


@pytest.fixture
async def fake():
    from fake_yandex import FakeYandex

    fy = FakeYandex()
    fy.base_url = await fy.start()
    yield fy
    await fy.close()


@pytest.fixture
async def make_adapter(mod, fake, monkeypatch):
    """Factory: an adapter wired to the fake API without calling ``connect()``."""
    from gateway.config import PlatformConfig

    created = []

    def factory(*, connected=True, bot_login="hermes_bot", bot_id="bot-guid", extra=None, **cfg):
        monkeypatch.delenv("YANDEX_MESSENGER_BOT_TOKEN", raising=False)
        config = PlatformConfig(enabled=True, token="TEST-TOKEN", extra=extra or {}, **cfg)
        adapter = mod.adapter.YandexMessengerAdapter(config)
        adapter._bot_login, adapter._bot_id = bot_login, bot_id
        if connected:
            adapter._client = mod.client.YandexMessengerClient(
                "TEST-TOKEN", base_url=fake.base_url, max_attempts=1, backoff_base=0.01)
            adapter._running = True
        adapter.events = []

        async def capture(event):
            adapter.events.append(event)

        adapter.handle_message = capture
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        for task in list(adapter._tasks):
            task.cancel()
        if adapter._poller is not None:
            await adapter._poller.stop()
        if adapter._client is not None:
            await adapter._client.close()


def update(text="hello", *, chat_id="0/0/dm-1", chat_type="private", login="alice", message_id=10, update_id=None,
           **extra):
    """Build a Bot API update dict."""
    upd = {
        "update_id": update_id if update_id is not None else message_id,
        "message_id": message_id,
        "timestamp": 1700000000,
        "chat": {"type": chat_type, "id": chat_id},
        "from": {"id": f"guid-{login}", "login": login, "display_name": login.title(), "robot": False},
    }
    if text is not None:
        upd["text"] = text
    upd.update(extra)
    return upd


@pytest.fixture
def make_update():
    return update
