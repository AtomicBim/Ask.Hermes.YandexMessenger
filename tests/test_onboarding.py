"""Welcome offering Mail / Calendar / Disk: Russian text, only for services without stored access, once a day."""
import datetime as dt
import os

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Isolated HERMES_HOME; alice is the only authorized user. Returns (home, secrets_dir, markers_dir)."""
    home = tmp_path / "home"
    (home / ".secrets").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("YANDEX_MESSENGER_ONBOARDING", raising=False)
    monkeypatch.delenv("YANDEX_MESSENGER_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("YANDEX_MESSENGER_ALLOWED_USERS", "alice")
    monkeypatch.setenv("HERMES_LANGUAGE", "en")  # the welcome must be Russian whatever the Hermes language is
    return home, home / ".secrets", home / "yandex_messenger" / "onboarded"


def _connect(secrets, *names):
    for name in names:
        (secrets / name).write_text('{"login": "x", "app_password": "p"}', encoding="utf-8")


def _welcomes(fake):
    return [r["json"]["text"] for r in fake.calls("messages/sendText") if "Привет" in r["json"]["text"]]


def _age_marker(markers, login, days):
    path = markers / login
    stamp = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


async def test_nothing_connected_greets_in_russian_listing_all_three(make_adapter, make_update, fake, env):
    a = make_adapter()
    await a._dispatch_update(make_update("hello", message_id=1))
    (text,) = _welcomes(fake)
    assert "подключи почту" in text and "подключи календарь" in text and "подключи диск" in text
    assert "Hi" not in text and "connect" not in text.lower()  # Russian only, even with HERMES_LANGUAGE=en
    assert [e.text for e in a.events] == ["hello"]  # the real message still reaches Hermes


async def test_partially_connected_lists_only_the_missing_ones(make_adapter, make_update, fake, env):
    _home, secrets, _markers = env
    _connect(secrets, "yandex-mail.json")
    await make_adapter()._dispatch_update(make_update("hi", message_id=1))
    (text,) = _welcomes(fake)
    assert "подключи календарь" in text and "подключи диск" in text
    assert "подключи почту" not in text and "подключено: Почта" in text


async def test_everything_connected_never_greets(make_adapter, make_update, fake, env):
    _home, secrets, markers = env
    _connect(secrets, "yandex-mail.json", "yandex-calendar.json", "yandex-disk.json")
    a = make_adapter()
    await a._dispatch_update(make_update("hi", message_id=1))
    assert _welcomes(fake) == [] and len(a.events) == 1
    assert not markers.exists() or list(markers.iterdir()) == []


async def test_once_per_day_then_again_next_day_until_complete(make_adapter, make_update, fake, env):
    _home, secrets, markers = env
    a = make_adapter()
    await a._dispatch_update(make_update("one", message_id=1))
    await a._dispatch_update(make_update("two", message_id=2))
    assert len(_welcomes(fake)) == 1  # second message of the same day: silent
    _age_marker(markers, "alice", 1)
    await a._dispatch_update(make_update("next day", message_id=3))
    assert len(_welcomes(fake)) == 2  # a new day, still not connected: remind
    _connect(secrets, "yandex-mail.json", "yandex-calendar.json", "yandex-disk.json")
    _age_marker(markers, "alice", 1)
    await a._dispatch_update(make_update("all done", message_id=4))
    assert len(_welcomes(fake)) == 2  # everything connected: never again


async def test_marker_survives_adapter_restart(make_adapter, make_update, fake, env):
    await make_adapter()._dispatch_update(make_update("one", message_id=1))
    await make_adapter()._dispatch_update(make_update("two", message_id=2))
    assert len(_welcomes(fake)) == 1


async def test_skipped_for_groups_commands_and_strangers(make_adapter, make_update, fake, env):
    _home, _secrets, markers = env
    a = make_adapter()
    await a._dispatch_update(make_update("@hermes_bot hi", chat_type="group", chat_id="0/0/grp", message_id=1))
    await a._dispatch_update(make_update("/help", message_id=2))
    await a._dispatch_update(make_update("hi", login="mallory", message_id=3))
    assert _welcomes(fake) == []
    assert not markers.exists() or list(markers.iterdir()) == []


async def test_can_be_disabled(make_adapter, make_update, fake, env, monkeypatch):
    monkeypatch.setenv("YANDEX_MESSENGER_ONBOARDING", "false")
    a = make_adapter()
    await a._dispatch_update(make_update("hello"))
    assert _welcomes(fake) == [] and len(a.events) == 1


async def test_failed_send_is_not_retried_and_never_blocks_the_message(make_adapter, make_update, fake, env, monkeypatch):
    _home, _secrets, markers = env
    monkeypatch.setenv("YANDEX_MESSENGER_ALLOWED_USERS", "Ev/il..Name")
    a = make_adapter()

    async def boom(*args, **kwargs):
        raise RuntimeError("send failed")

    a.send = boom
    await a._dispatch_update(make_update("hi", login="Ev/il..Name", message_id=1))
    assert [p.name for p in markers.iterdir()] == ["ev_il..name"]  # sanitized, no path traversal
    assert len(a.events) == 1
    await make_adapter()._dispatch_update(make_update("hi", login="Ev/il..Name", message_id=2))
    assert _welcomes(fake) == []  # the marker was written before the failed send: no repeat today


async def test_secret_contents_are_never_read(make_adapter, make_update, fake, env, monkeypatch):
    """Only names and sizes decide 'connected': reading a secret file would be a leak vector."""
    _home, secrets, _markers = env
    _connect(secrets, "yandex-mail.json")
    opened = []
    real_open = open

    def spy(file, *args, **kwargs):
        if ".secrets" in str(file):
            opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", spy)
    await make_adapter()._dispatch_update(make_update("hi", message_id=1))
    assert opened == []


def test_connection_status_name_matching(mod, tmp_path):
    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    status = mod.onboarding.connection_status
    assert status(tmp_path).missing == ("mail", "calendar", "disk")  # empty directory
    assert status(None).missing == ("mail", "calendar", "disk")      # no Hermes home
    (secrets / "yandex-webdav.netrc").write_text("machine x password y")   # legacy name of a Disk user
    (secrets / "yd.sh").write_text("#!/bin/sh\n")                           # helper script of the same user
    (secrets / "imap_credentials.txt").write_text("p")
    (secrets / "empty-calendar.json").write_text("")                        # empty file = not connected
    (secrets / "dyd.txt").write_text("p")                                   # "yd" only counts as a whole word
    (secrets / "mail").mkdir()                                              # a directory is not an access file
    result = status(tmp_path)
    assert result.connected == ("mail", "disk") and result.missing == ("calendar",)
    # "yd" must match only as a whole word: with the real Disk files gone, "dyd.txt" alone must not connect Disk
    (secrets / "yd.sh").unlink()
    (secrets / "yandex-webdav.netrc").unlink()
    assert status(tmp_path).missing == ("calendar", "disk")


def test_welcome_text_is_russian_and_mentions_only_missing(mod):
    status = mod.onboarding.Status(connected=("disk",), missing=("mail", "calendar"))
    text = mod.onboarding.welcome_text(status)
    assert "Привет" in text and "подключено: Диск" in text
    assert "подключи почту" in text and "подключи календарь" in text and "подключи диск" not in text
