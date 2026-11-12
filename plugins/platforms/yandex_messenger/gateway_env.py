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


# Slash commands a non-admin may run once admin gating is on (``/help`` and ``/whoami`` are always allowed).
# Deliberately excluded: /model /config /reasoning /fast /personality /tools /skills /yolo /approvals /plugins
# /reload* /restart /update /sethome /cron /login ... - everything that changes how the agent runs.
DEFAULT_USER_COMMANDS = (
    "new", "stop", "status", "retry", "undo", "history", "title", "resume", "sessions", "queue", "usage",
    "approve", "deny", "version",
)


def _admin_gating(config) -> None:
    """``YANDEX_MESSENGER_ADMIN_USERS`` -> Hermes slash-command gating (``gateway/slash_access.py``).

    Listed logins keep every command (DM and group). Everybody else who is allowed to talk to the bot gets
    ``YANDEX_MESSENGER_USER_COMMANDS`` (default: ``DEFAULT_USER_COMMANDS``) plus ``/help`` and ``/whoami``.
    Unset -> no gating: every allowed user can run every command, including ``/model`` (Hermes' own default).
    """
    admins = csv_list(getenv("YANDEX_MESSENGER_ADMIN_USERS") or "")
    if not admins:
        return
    extra = config.platforms.setdefault(_PLATFORM, PlatformConfig()).extra
    extra["allow_admin_from"] = admins
    extra["group_allow_admin_from"] = admins
    raw = getenv("YANDEX_MESSENGER_USER_COMMANDS")
    commands = csv_list(raw) if raw is not None and raw.strip() else list(DEFAULT_USER_COMMANDS)
    extra["user_allowed_commands"] = commands
    extra["group_user_allowed_commands"] = commands


ENV_ENABLE_CREDENTIALS = ("YANDEX_MESSENGER_BOT_TOKEN",)
# Order matters: allowed_chats / admin gating may create the entry the home channel then attaches to.
ENV_OVERRIDES = (
    EnvCred(_PLATFORM, ("YANDEX_MESSENGER_BOT_TOKEN",), token="YANDEX_MESSENGER_BOT_TOKEN"),
    _allowed_chats,
    _admin_gating,
    home_step(_PLATFORM, "YANDEX_MESSENGER_HOME_CHANNEL"),
)

AUTHZ = {
    "allowed_users_env": "YANDEX_MESSENGER_ALLOWED_USERS",
    "allow_all_env": "YANDEX_MESSENGER_ALLOW_ALL_USERS",
    "allowlist_suppresses_pairing": True,
}
