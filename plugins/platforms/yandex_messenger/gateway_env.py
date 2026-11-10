"""Yandex Messenger env -> gateway config bridge (``PlatformEntry.env_overrides``).

Import only ``gateway.*`` here: core reads this file without the adapter when the
plugin is not loaded (safe mode, plugin disabled).
"""

from gateway.config import Platform, PlatformConfig
from gateway.platform_env import EnvCred, csv_list, getenv, home_step

PLATFORM_NAME = "yandex_messenger"


def platform() -> Platform:
    """The ``Platform`` member for this plugin.

    ``Platform("<name>")`` only resolves for bundled plugins or once the platform registry knows
    the name. This file may be read before ``register()`` ran (core reads authz names from it), so
    fall back to creating the dynamic member the same way ``Platform._missing_`` does.
    """
    try:
        return Platform(PLATFORM_NAME)
    except ValueError:
        add = getattr(Platform, "_add_pseudo_member", None)
        if add is None:
            raise
        return add(PLATFORM_NAME)


_PLATFORM = platform()


def _allowed_chats(config) -> None:
    if chats := getenv("YANDEX_MESSENGER_ALLOWED_CHATS"):
        config.platforms.setdefault(_PLATFORM, PlatformConfig()).extra["allowed_chats"] = csv_list(chats)


ENV_ENABLE_CREDENTIALS = ("YANDEX_MESSENGER_BOT_TOKEN",)
# Order matters: allowed_chats may create the entry the home channel then attaches to.
ENV_OVERRIDES = (
    EnvCred(_PLATFORM, ("YANDEX_MESSENGER_BOT_TOKEN",), token="YANDEX_MESSENGER_BOT_TOKEN"),
    _allowed_chats,
    home_step(_PLATFORM, "YANDEX_MESSENGER_HOME_CHANNEL"),
)

AUTHZ = {
    "allowed_users_env": "YANDEX_MESSENGER_ALLOWED_USERS",
    "allow_all_env": "YANDEX_MESSENGER_ALLOW_ALL_USERS",
    "allowlist_suppresses_pairing": True,
}
