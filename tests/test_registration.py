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


def _apply_env(mod):
    from gateway.config import GatewayConfig, Platform

    config = GatewayConfig()
    for step in mod.gateway_env.ENV_OVERRIDES:
        (step.apply if hasattr(step, "apply") else step)(config)
    return config.platforms.get(Platform("yandex_messenger"))


def test_admin_gating_is_off_by_default(mod, monkeypatch):
    for name in ("YANDEX_MESSENGER_ADMIN_USERS", "YANDEX_MESSENGER_USER_COMMANDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("YANDEX_MESSENGER_BOT_TOKEN", "t")
    extra = _apply_env(mod).extra
    assert "allow_admin_from" not in extra and "user_allowed_commands" not in extra


def test_admin_gating_blocks_model_for_users_but_not_admins(mod, monkeypatch):
    """End to end with Hermes' own policy code: the env vars must really lock /model for non-admins."""
    from gateway.slash_access import policy_from_extra

    monkeypatch.setenv("YANDEX_MESSENGER_BOT_TOKEN", "t")
    monkeypatch.setenv("YANDEX_MESSENGER_ADMIN_USERS", "boss@corp.ru, ops@corp.ru")
    monkeypatch.delenv("YANDEX_MESSENGER_USER_COMMANDS", raising=False)
    extra = _apply_env(mod).extra
    assert extra["allow_admin_from"] == ["boss@corp.ru", "ops@corp.ru"]
    for scope in ("dm", "group"):
        policy = policy_from_extra(extra, scope)
        assert policy.enabled and policy.is_admin("boss@corp.ru") and not policy.is_admin("alice@corp.ru")
        for cmd in ("model", "config", "reasoning", "fast", "personality", "tools", "skills", "yolo", "approvals",
                    "plugins", "reload", "restart", "update", "sethome", "cron", "login", "profile"):
            assert policy.can_run("boss@corp.ru", cmd), cmd
            assert not policy.can_run("alice@corp.ru", cmd), cmd
        for cmd in ("help", "whoami", "new", "stop", "status", "retry", "approve", "deny"):
            assert policy.can_run("alice@corp.ru", cmd), cmd
    dm = policy_from_extra(extra, "dm")
    assert not dm.is_admin("") and not dm.is_admin(None) and not dm.can_run("", "model")  # no id: never an admin


def test_default_user_commands_are_all_real_and_safe(mod):
    """Every default name resolves to a registered command, none of them is a settings command."""
    from hermes_cli.commands import resolve_command

    unsafe = {"model", "config", "reasoning", "fast", "personality", "tools", "toolsets", "skills", "yolo",
              "approvals", "plugins", "reload", "reload-mcp", "restart", "update", "sethome", "cron", "login",
              "profile", "memory", "voice", "verbose", "codex-runtime"}
    for name in mod.gateway_env.DEFAULT_USER_COMMANDS:
        command = resolve_command(name)
        assert command is not None, f"/{name} is not a Hermes command"
        assert command.name not in unsafe


def test_user_commands_override(mod, monkeypatch):
    monkeypatch.setenv("YANDEX_MESSENGER_BOT_TOKEN", "t")
    monkeypatch.setenv("YANDEX_MESSENGER_ADMIN_USERS", "boss@corp.ru")
    monkeypatch.setenv("YANDEX_MESSENGER_USER_COMMANDS", "/new, STATUS")
    extra = _apply_env(mod).extra
    assert extra["user_allowed_commands"] == ["/new", "STATUS"]  # Hermes strips "/" and lower-cases itself
    from gateway.slash_access import policy_from_extra

    policy = policy_from_extra(extra, "dm")
    assert policy.can_run("alice", "new") and policy.can_run("alice", "status") and not policy.can_run("alice", "stop")
    monkeypatch.setenv("YANDEX_MESSENGER_USER_COMMANDS", "   ")  # blank -> the safe default again
    assert "stop" in _apply_env(mod).extra["user_allowed_commands"]


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
