"""register(ctx), env -> config bridging and authorization env names against the real Hermes."""
import pytest


class _Ctx:
    def __init__(self):
        self.calls = []

    def register_platform(self, **kwargs):
        self.calls.append(kwargs)


def test_register_passes_valid_kwargs(plugin):
    ctx = _Ctx()
    plugin.register(ctx)
    (kw,) = ctx.calls
    assert kw["name"] == "yandex_messenger" and kw["label"] == "Yandex Messenger"
    assert kw["required_env"] == ["YANDEX_MESSENGER_BOT_TOKEN"]
    assert kw["cron_deliver_env_var"] == "YANDEX_MESSENGER_HOME_CHANNEL"
    assert kw["max_message_length"] == 6000
    assert kw["allowed_users_env"] == "YANDEX_MESSENGER_ALLOWED_USERS"
    assert kw["allow_all_env"] == "YANDEX_MESSENGER_ALLOW_ALL_USERS"
    assert callable(kw["standalone_sender_fn"]) and callable(kw["adapter_factory"])
    assert "Yandex Messenger" in kw["platform_hint"] and "__italic__" in kw["platform_hint"]


def test_register_builds_a_real_platform_entry(plugin):
    """Every kwarg must be accepted by the installed ``PlatformEntry`` (unknown keys raise TypeError)."""
    from gateway.platform_registry import PlatformEntry

    ctx = _Ctx()
    plugin.register(ctx)
    kw = dict(ctx.calls[0])
    entry = PlatformEntry(
        name=kw.pop("name"), label=kw.pop("label"), adapter_factory=kw.pop("adapter_factory"),
        check_fn=kw.pop("check_fn"), validate_config=kw.pop("validate_config"),
        required_env=kw.pop("required_env"), install_hint=kw.pop("install_hint"), source="plugin", **kw)
    assert entry.name == "yandex_messenger"
    assert entry.check_fn() is True


def test_platform_enum_is_dynamic(plugin):
    from gateway.config import Platform

    assert Platform("yandex_messenger") is Platform("yandex_messenger")
    assert Platform("yandex_messenger").value == "yandex_messenger"


def test_env_overrides_create_platform_config(mod, monkeypatch):
    from gateway.config import GatewayConfig, Platform

    monkeypatch.setenv("YANDEX_MESSENGER_BOT_TOKEN", "tok-1")
    monkeypatch.setenv("YANDEX_MESSENGER_ALLOWED_CHATS", "0/0/a, 0/0/b")
    monkeypatch.setenv("YANDEX_MESSENGER_HOME_CHANNEL", "chat:0/0/home")
    monkeypatch.setenv("YANDEX_MESSENGER_HOME_CHANNEL_NAME", "Ops")
    config = GatewayConfig()
    for step in mod.gateway_env.ENV_OVERRIDES:
        if hasattr(step, "apply"):
            step.apply(config)
        else:
            step(config)
    pcfg = config.platforms[Platform("yandex_messenger")]
    assert pcfg.token == "tok-1" and pcfg.enabled
    assert pcfg.extra["allowed_chats"] == ["0/0/a", "0/0/b"]
    assert pcfg.home_channel.chat_id == "chat:0/0/home" and pcfg.home_channel.name == "Ops"


def test_authz_names(mod):
    assert mod.gateway_env.AUTHZ["allowed_users_env"] == "YANDEX_MESSENGER_ALLOWED_USERS"
    assert mod.gateway_env.AUTHZ["allow_all_env"] == "YANDEX_MESSENGER_ALLOW_ALL_USERS"
    assert mod.gateway_env.ENV_ENABLE_CREDENTIALS == ("YANDEX_MESSENGER_BOT_TOKEN",)


def test_settings_precedence_and_defaults(mod, monkeypatch):
    from gateway.config import PlatformConfig

    for name in ("YANDEX_MESSENGER_REQUIRE_MENTION", "YANDEX_MESSENGER_POLL_INTERVAL",
                 "YANDEX_MESSENGER_MAX_FILE_MB", "YANDEX_MESSENGER_WEBHOOK_PATH"):
        monkeypatch.delenv(name, raising=False)
    cfg = PlatformConfig(token="t", extra={"require_mention": False, "max_file_mb": 10, "allowed_chats": "x,y"})
    s = mod.settings.load_settings(cfg)
    assert s.token == "t" and s.require_mention is False and s.max_file_mb == 10
    assert s.allowed_chats == frozenset({"x", "y"}) and s.poll_interval == 1.0
    assert s.webhook_port == 8647 and s.webhook_path == "/yandex-messenger/webhook" and not s.webhook_mode
    monkeypatch.setenv("YANDEX_MESSENGER_REQUIRE_MENTION", "true")  # env beats extra
    monkeypatch.setenv("YANDEX_MESSENGER_WEBHOOK_PATH", "hooks/ym/")
    s = mod.settings.load_settings(cfg)
    assert s.require_mention is True and s.webhook_path == "/hooks/ym"


def test_check_and_validate(mod, monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.delenv("YANDEX_MESSENGER_BOT_TOKEN", raising=False)
    assert mod.adapter.check_requirements() is True
    assert mod.adapter.validate_config(PlatformConfig(token="x")) is True
    assert mod.adapter.validate_config(PlatformConfig()) is False
    monkeypatch.setenv("YANDEX_MESSENGER_BOT_TOKEN", "from-env")
    assert mod.adapter.validate_config(PlatformConfig()) is True
