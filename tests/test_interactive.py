"""Buttons: payload shape, callback dispatch to (mocked) Hermes resolvers, auth, races, pickers."""
import sys
import types

import pytest
from aiohttp import web

CHAT = "0/0/dm-1"


def tap_update(cb, *, login="alice", chat_id=CHAT, chat_type="private", name="hermes", update_id=None):
    return {
        "update_id": update_id or 9000, "message_id": 1, "chat": {"type": chat_type, "id": chat_id},
        "from": {"id": f"guid-{login}", "login": login, "display_name": login.title(), "robot": False},
        "bot_request": {"server_action": {"name": name, "payload": {"cb": cb}}, "element_id": "b1_1"},
    }


def buttons_of(body):
    kb = body["suggest_buttons"]
    return [b for row in kb["buttons"] for b in row]


def cb_of(button):
    return button["directives"][0]["payload"]["cb"]


@pytest.fixture
def auth_alice(make_adapter):
    """Adapter whose runner-injected auth check admits only ``alice``."""

    def build(**kw):
        a = make_adapter(**kw)
        a._authorization_check = lambda user, chat_type=None, chat_id=None, **k: user == "alice"
        a._chat_types[CHAT] = "private"
        return a

    return build


@pytest.fixture
def approvals(monkeypatch):
    class Calls(list):
        result = {"count": 1}

    calls = Calls()

    def resolve_gateway_approval(session_key, choice, resolve_all=False, reason=None, request_id=None):
        calls.append((session_key, choice, request_id))
        return calls.result["count"]

    monkeypatch.setattr("tools.approval.resolve_gateway_approval", resolve_gateway_approval)
    return calls


def edits(fake):
    """sendText calls that edit an existing message."""
    return [c["json"] for c in fake.calls("messages/sendText") if "message_id" in c["json"]]


def sends(fake):
    return [c["json"] for c in fake.calls("messages/sendText") if "message_id" not in c["json"]]


# ---------------------------------------------------------------- approval
async def test_approval_card_payload(auth_alice, fake):
    a = auth_alice()
    assert type(a).supports_exec_approval_buttons() is True
    res = await a.send_exec_approval(
        CHAT, "rm -rf /tmp/x", "sess-1", description="deletes files", request_id="req-1", allow_permanent=True)
    assert res.success
    body = fake.last("messages/sendText")["json"]
    assert "rm -rf /tmp/x" in body["text"] and "```" in body["text"]
    assert body["important"] is True and body["chat_id"] == CHAT
    kb = body["suggest_buttons"]
    assert kb["persist"] is True and kb["layout"] == "true"
    assert [len(row) for row in kb["buttons"]] == [2, 2]  # once/session | always/deny
    flat = buttons_of(body)
    assert [cb_of(b) for b in flat] == ["ea:once:1", "ea:session:1", "ea:always:1", "ea:deny:1"]
    ids = [b["id"] for b in flat]
    assert len(ids) == len(set(ids)) and all(len(i) <= 255 for i in ids)
    for b in flat:
        action, state = b["directives"]
        assert action["type"] == "server_action" and action["name"] == "hermes"
        assert state["type"] == "set_elements_state" and state["state"] == "disabled"
        assert sorted(state["ids"]) == sorted(ids) and state["timeout_seconds"] <= 60
    assert a._approval_state == {1: "sess-1"} and a._approval_request_ids == {1: "req-1"}


async def test_approval_tap_resolves_and_edits_card(auth_alice, fake, approvals):
    a = auth_alice()
    await a.send_exec_approval(CHAT, "ls", "sess-1", request_id="req-1")
    a._typing_paused.add(CHAT)
    await a._handle_bot_request(tap_update("ea:once:1"))
    assert approvals == [("sess-1", "once", "req-1")]
    (edit,) = edits(fake)
    assert edit["message_id"] == int(a._cards[("ea", 1)]["message_id"]) and edit["chat_id"] == CHAT
    assert edit["suggest_buttons"]["buttons"] == []  # buttons dropped
    assert "ls" in edit["text"] and "Allowed once" in edit["text"] and "Alice" in edit["text"]
    assert CHAT not in a._typing_paused  # typing resumes after the approval
    assert a._approval_state == {} and a._approval_request_ids == {}


async def test_approval_double_tap_is_a_noop_with_notice(auth_alice, fake, approvals):
    a = auth_alice()
    await a.send_exec_approval(CHAT, "ls", "sess-1", request_id="req-1")
    await a._handle_bot_request(tap_update("ea:once:1"))
    await a._handle_bot_request(tap_update("ea:deny:1", update_id=9001))
    assert approvals == [("sess-1", "once", "req-1")]  # the second tap never reaches Hermes
    notice = fake.last("messages/sendText")["json"]
    assert notice["login"] == "alice" and "already resolved" in notice["text"]


async def test_approval_unauthorized_tap(auth_alice, fake, approvals):
    a = auth_alice(bot_login="hermes_bot")
    await a.send_exec_approval(CHAT, "ls", "sess-1", request_id="req-1")
    n_edits = len(edits(fake))
    await a._handle_bot_request(tap_update("ea:once:1", login="mallory"))
    assert approvals == [] and a._approval_state == {1: "sess-1"}  # prompt stays pending for the real owner
    assert len(edits(fake)) == n_edits  # shared card untouched
    notice = fake.last("messages/sendText")["json"]
    assert notice["login"] == "mallory" and "not authorized" in notice["text"]


async def test_approval_expired_when_nothing_pending(auth_alice, fake, approvals):
    approvals.result["count"] = 0
    a = auth_alice()
    await a.send_exec_approval(CHAT, "ls", "sess-1", request_id="req-1")
    a._typing_paused.add(CHAT)
    await a._handle_bot_request(tap_update("ea:once:1"))
    (edit,) = edits(fake)
    assert "expired" in edit["text"] and "Allowed once" not in edit["text"]
    assert CHAT in a._typing_paused  # nothing was approved, typing stays paused


async def test_approval_forged_choice_is_rejected(auth_alice, fake, approvals):
    a = auth_alice()
    await a.send_exec_approval(CHAT, "ls", "sess-1", smart_denied=True, request_id="r")
    await a._handle_bot_request(tap_update("ea:always:1"))  # smart deny offers no "always"
    assert approvals == [] and a._approval_state == {1: "sess-1"}
    assert "Invalid" in fake.last("messages/sendText")["json"]["text"]
    await a._handle_bot_request(tap_update("ea:once:abc", update_id=9002))
    assert approvals == []


async def test_card_finish_falls_back_when_empty_keyboard_is_rejected(auth_alice, fake, approvals):
    a = auth_alice()
    await a.send_exec_approval(CHAT, "ls", "sess-1", request_id="req-1")
    fake.responses["messages/sendText"] = [
        lambda r, rec: web.json_response({"ok": False, "description": "invalid suggest_buttons"}, status=400),
        {"ok": True, "message_id": 1},
    ]
    await a._handle_bot_request(tap_update("ea:deny:1"))
    tried = edits(fake)
    assert len(tried) == 2 and "suggest_buttons" in tried[0] and "suggest_buttons" not in tried[1]


async def test_approval_send_failure_returns_failed_result(auth_alice, fake):
    a = auth_alice()
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response({"ok": False, "description": "x"}, status=400)
    res = await a.send_exec_approval(CHAT, "ls", "sess-1")
    assert not res.success and a._approval_state == {}  # the gateway then falls back to the text prompt


# ---------------------------------------------------------------- slash confirm
async def test_slash_confirm_flow(auth_alice, fake, monkeypatch):
    a = auth_alice()
    resolved = []

    async def resolve(session_key, confirm_id, choice, timeout=0):
        resolved.append((session_key, confirm_id, choice))
        return "MCP servers reloaded"

    monkeypatch.setattr("tools.slash_confirm.resolve", resolve)
    res = await a.send_slash_confirm(CHAT, "Reload MCP?", "This restarts servers.", "sess-9", "cid-1")
    assert res.success
    flat = buttons_of(fake.last("messages/sendText")["json"])
    assert [cb_of(b) for b in flat] == ["sc:once:cid-1", "sc:always:cid-1", "sc:cancel:cid-1"]
    await a._handle_bot_request(tap_update("sc:once:cid-1"))
    assert resolved == [("sess-9", "cid-1", "once")]
    assert "Approved once" in edits(fake)[-1]["text"]
    assert sends(fake)[-1]["text"] == "MCP servers reloaded"  # the handler's output is posted as a follow-up
    await a._handle_bot_request(tap_update("sc:cancel:cid-1", update_id=9001))
    assert len(resolved) == 1  # double tap


async def test_slash_confirm_unauthorized(auth_alice, fake, monkeypatch):
    a = auth_alice()

    async def resolve(*args, **kwargs):  # pragma: no cover
        raise AssertionError("must not run")

    monkeypatch.setattr("tools.slash_confirm.resolve", resolve)
    await a.send_slash_confirm(CHAT, "t", "m", "sess", "cid-2")
    await a._handle_bot_request(tap_update("sc:once:cid-2", login="eve"))
    assert a._slash_confirm_state == {"cid-2": "sess"}


# ---------------------------------------------------------------- clarify
async def test_clarify_buttons_and_resolution(auth_alice, fake, monkeypatch):
    a = auth_alice()
    entry = types.SimpleNamespace(choices=["red", "green", "blue"])
    monkeypatch.setattr("tools.clarify_gateway.get_pending", lambda cid: entry)
    resolved = []
    monkeypatch.setattr("tools.clarify_gateway.resolve_gateway_clarify", lambda cid, text: resolved.append((cid, text)) or True)
    await a.send_clarify(CHAT, "Pick a colour", ["red", "green", "blue"], "clar-1", "sess")
    body = fake.last("messages/sendText")["json"]
    assert "1. red" in body["text"] and "3. blue" in body["text"]
    flat = buttons_of(body)
    assert [cb_of(b) for b in flat] == ["cl:clar-1:0", "cl:clar-1:1", "cl:clar-1:2", "cl:clar-1:other"]
    assert [b["title"] for b in flat[:3]] == ["1", "2", "3"]
    await a._handle_bot_request(tap_update("cl:clar-1:1"))
    assert resolved == [("clar-1", "green")]
    assert "Alice:** green" in edits(fake)[-1]["text"].replace("**", "**")  # resolved line on the card
    assert "clar-1" not in a._clarify_state


async def test_clarify_other_flips_to_text_capture(auth_alice, fake, monkeypatch):
    a = auth_alice()
    flipped = []
    monkeypatch.setattr("tools.clarify_gateway.mark_awaiting_text", lambda cid: flipped.append(cid) or True)
    await a.send_clarify(CHAT, "Which?", ["a", "b"], "clar-2", "sess")
    await a._handle_bot_request(tap_update("cl:clar-2:other"))
    assert flipped == ["clar-2"]
    assert "next message" in edits(fake)[-1]["text"]
    assert "clar-2" in a._clarify_state  # still pending until the typed answer arrives


async def test_clarify_expired_entry(auth_alice, fake, monkeypatch):
    a = auth_alice()
    monkeypatch.setattr("tools.clarify_gateway.get_pending", lambda cid: None)
    monkeypatch.setattr("tools.clarify_gateway.resolve_gateway_clarify", lambda cid, text: False)
    await a.send_clarify(CHAT, "Which?", ["a"], "clar-3", "sess")
    await a._handle_bot_request(tap_update("cl:clar-3:0"))
    assert "expired" in edits(fake)[-1]["text"]


async def test_clarify_without_choices_has_no_keyboard(auth_alice, fake):
    a = auth_alice()
    await a.send_clarify(CHAT, "What is your name?", None, "clar-4", "sess")
    body = fake.last("messages/sendText")["json"]
    assert body["text"] == "❓ What is your name?" and "suggest_buttons" not in body


# ---------------------------------------------------------------- update prompt
async def test_update_prompt_writes_response(auth_alice, fake):
    from hermes_constants import get_hermes_home

    a = auth_alice()
    res = await a.send_update_prompt(CHAT, "Install update?", default="y")
    assert res.success
    flat = buttons_of(fake.last("messages/sendText")["json"])
    assert [cb_of(b) for b in flat] == ["update_prompt:y", "update_prompt:n"]
    await a._handle_bot_request(tap_update("update_prompt:n"))
    assert (get_hermes_home() / ".update_response").read_text() == "n"
    assert "No" in edits(fake)[-1]["text"]


async def test_update_prompt_unauthorized(auth_alice, fake):
    from hermes_constants import get_hermes_home

    a = auth_alice()
    marker = get_hermes_home() / ".update_response"
    marker.unlink(missing_ok=True)
    await a.send_update_prompt(CHAT, "Install update?")
    await a._handle_bot_request(tap_update("update_prompt:y", login="eve"))
    assert not marker.exists()


# ---------------------------------------------------------------- choice picker
async def test_choice_picker_flow(auth_alice, fake):
    a = auth_alice()
    chosen = []

    async def on_choice(chat_id, value):
        chosen.append((chat_id, value))
        return f"Reasoning set to **{value}**"

    choices = [{"value": "low", "label": "Low"}, {"value": "high", "label": "High", "is_current": True},
               {"value": "xhigh", "label": "XHigh"}]
    res = await a.send_choice_picker(CHAT, "Reasoning effort", choices, "sess", on_choice)
    assert res.success
    flat = buttons_of(fake.last("messages/sendText")["json"])
    assert [b["title"] for b in flat] == ["Low", "✓ High", "XHigh"] and [cb_of(b) for b in flat] == ["cp:0", "cp:1", "cp:2"]
    await a._handle_bot_request(tap_update("cp:2"))
    assert chosen == [(CHAT, "xhigh")]
    assert "Reasoning set to **xhigh**" in edits(fake)[-1]["text"] and CHAT not in a._choice_picker_state
    await a._handle_bot_request(tap_update("cp:1", update_id=9001))
    assert chosen == [(CHAT, "xhigh")]  # expired picker: a double tap does not apply twice
    assert "expired" in fake.last("messages/sendText")["json"]["text"]


async def test_choice_picker_requires_authorization(auth_alice, fake):
    a = auth_alice()

    async def on_choice(chat_id, value):  # pragma: no cover
        raise AssertionError("stranger must not drive the picker")

    await a.send_choice_picker(CHAT, "t", [{"value": "v", "label": "V"}], "sess", on_choice)
    await a._handle_bot_request(tap_update("cp:0", login="eve"))
    assert CHAT in a._choice_picker_state


async def test_choice_picker_without_choices(auth_alice):
    a = auth_alice()
    res = await a.send_choice_picker(CHAT, "t", [], "sess", None)
    assert not res.success


# ---------------------------------------------------------------- model picker
PROVIDERS = [
    {"slug": "alpha", "name": "Alpha", "is_current": True, "total_models": 3, "models": ["a/one", "a/two", "a/three"]},
    {"slug": "beta", "name": "Beta", "total_models": 1, "models": ["b-one"]},
]


@pytest.fixture
def no_model_warning(monkeypatch):
    monkeypatch.setattr("hermes_cli.model_selection_guards.combined_selection_warning", lambda *a, **k: None)


async def test_model_picker_drill_down_and_switch(auth_alice, fake, no_model_warning):
    a = auth_alice()
    picked = []

    async def on_model(chat_id, model_id, provider_slug):
        picked.append((chat_id, model_id, provider_slug))
        return f"Switched to `{model_id}`"

    res = await a.send_model_picker(CHAT, PROVIDERS, "a/one", "alpha", "sess", on_model)
    assert res.success
    body = fake.last("messages/sendText")["json"]
    first = {cb_of(b) for b in buttons_of(body)}
    assert {"mp:alpha", "mp:beta", "mx"} <= first and "a/one" in body["text"]
    await a._handle_bot_request(tap_update("mp:alpha"))
    edit = edits(fake)[-1]
    assert edit["message_id"] == int(res.message_id) and "suggest_buttons" in edit
    cbs = [cb_of(b) for b in buttons_of(edit)]
    assert cbs[:3] == ["mm:0", "mm:1", "mm:2"] and "mb" in cbs and "mx" in cbs
    ids_before = {b["id"] for b in buttons_of(body)}
    assert not ids_before & {b["id"] for b in buttons_of(edit)}  # a re-render never reuses disabled element ids
    await a._handle_bot_request(tap_update("mm:1", update_id=9001))
    assert picked == [(CHAT, "a/two", "alpha")]
    assert "Switched to" in edits(fake)[-1]["text"] and CHAT not in a._model_picker_state


async def test_model_picker_back_cancel_and_pagination(auth_alice, fake, no_model_warning):
    a = auth_alice()
    providers = [{"slug": f"p{i:02d}", "name": f"P{i}", "total_models": 1, "models": ["m"]} for i in range(25)]

    async def on_model(*args):  # pragma: no cover
        raise AssertionError

    await a.send_model_picker(CHAT, providers, "m", "p00", "sess", on_model)
    cbs = [cb_of(b) for b in buttons_of(fake.last("messages/sendText")["json"])]
    assert "mpv:1" in cbs and "mx:noop" in cbs
    await a._handle_bot_request(tap_update("mpv:2"))
    cbs = [cb_of(b) for b in buttons_of(edits(fake)[-1])]
    assert "mpv:1" in cbs and "mpv:3" not in cbs and a._model_picker_state[CHAT]["provider_page"] == 2
    await a._handle_bot_request(tap_update("mp:p21", update_id=9001))
    await a._handle_bot_request(tap_update("mb", update_id=9002))
    assert "mp:p20" in [cb_of(b) for b in buttons_of(edits(fake)[-1])]
    await a._handle_bot_request(tap_update("mx:noop", update_id=9003))
    await a._handle_bot_request(tap_update("mx", update_id=9004))
    assert CHAT not in a._model_picker_state and "cancelled" in edits(fake)[-1]["text"]


async def test_model_picker_warning_asks_for_confirmation(auth_alice, fake, monkeypatch):
    a = auth_alice()
    picked = []

    async def on_model(chat_id, model_id, slug):
        picked.append(model_id)
        return "ok"

    warning = types.SimpleNamespace(title="Expensive model", message="costs a lot")
    monkeypatch.setattr("hermes_cli.model_selection_guards.combined_selection_warning", lambda *a, **k: warning)
    await a.send_model_picker(CHAT, PROVIDERS, "a/one", "alpha", "sess", on_model)
    await a._handle_bot_request(tap_update("mp:alpha"))
    await a._handle_bot_request(tap_update("mm:0", update_id=9001))
    assert picked == [] and "Expensive model" in edits(fake)[-1]["text"]
    assert "mc:0" in [cb_of(b) for b in buttons_of(edits(fake)[-1])]
    await a._handle_bot_request(tap_update("mc:0", update_id=9002))
    assert picked == ["a/one"]


async def test_model_picker_edit_failure_falls_back_to_delete_and_resend(auth_alice, fake, no_model_warning):
    a = auth_alice()

    async def on_model(*args):  # pragma: no cover
        return "x"

    res = await a.send_model_picker(CHAT, PROVIDERS, "a/one", "alpha", "sess", on_model)
    fake.responses["messages/sendText"] = [
        lambda r, rec: web.json_response({"ok": False, "description": "cannot edit buttons"}, status=400),
        {"ok": True, "message_id": 4242},
    ]
    await a._handle_bot_request(tap_update("mp:alpha"))
    assert fake.last("messages/delete")["json"]["message_id"] == int(res.message_id)
    assert a._model_picker_state[CHAT]["msg_id"] == "4242"  # follows the re-sent card


async def test_model_picker_expired_and_unauthorized(auth_alice, fake, no_model_warning):
    a = auth_alice()
    await a._handle_bot_request(tap_update("mp:alpha"))  # nothing was ever opened
    assert "expired" in fake.last("messages/sendText")["json"]["text"]

    async def on_model(*args):  # pragma: no cover
        raise AssertionError

    await a.send_model_picker(CHAT, PROVIDERS, "a/one", "alpha", "sess", on_model)
    n = len(fake.calls("messages/sendText"))
    await a._handle_bot_request(tap_update("mp:alpha", login="eve", update_id=9001))
    notice = fake.calls("messages/sendText")[n]["json"]
    assert notice["login"] == "eve" and "not authorized" in notice["text"]
    assert "selected_provider" not in a._model_picker_state[CHAT]


# ---------------------------------------------------------------- dispatcher / auth
async def test_foreign_and_malformed_taps_are_ignored(auth_alice, fake, caplog):
    a = auth_alice()
    n = len(fake.requests)
    await a._handle_bot_request(tap_update("ea:once:1", name="someone_else"))
    await a._handle_bot_request({"chat": {"id": CHAT}, "from": {"login": "alice"},
                                 "bot_request": {"server_action": {"name": "hermes", "payload": {}}}})
    await a._handle_bot_request({"from": {"login": "alice"},
                                 "bot_request": {"server_action": {"name": "hermes", "payload": {"cb": "mx"}}}})
    assert len(fake.requests) == n
    with caplog.at_level("WARNING"):
        upd = tap_update("zzz")
        upd["bot_request"]["errors"] = [{"type": "unsupported_directive", "name": "set_elements_state"}]
        await a._handle_bot_request(upd)
    assert "unsupported_directive" in caplog.text


async def test_env_allowlist_fallback_is_fail_closed(make_adapter, monkeypatch, mod):
    a = make_adapter()
    a._authorization_check = None
    tap = mod.interactive.Tap(chat_id=CHAT, chat_type="private", login="Alice", name="Alice")
    for var in ("YANDEX_MESSENGER_ALLOWED_USERS", "YANDEX_MESSENGER_ALLOW_ALL_USERS", "GATEWAY_ALLOW_ALL_USERS"):
        monkeypatch.delenv(var, raising=False)
    assert a._is_tap_user_authorized(tap) is False  # nothing configured -> deny
    monkeypatch.setenv("YANDEX_MESSENGER_ALLOWED_USERS", "alice, bob")
    assert a._is_tap_user_authorized(tap) is True  # logins compare case-insensitively
    assert a._is_tap_user_authorized(mod.interactive.Tap(CHAT, "private", "carol", "Carol")) is False
    monkeypatch.delenv("YANDEX_MESSENGER_ALLOWED_USERS")
    monkeypatch.setenv("YANDEX_MESSENGER_ALLOW_ALL_USERS", "true")
    assert a._is_tap_user_authorized(tap) is True
    assert a._is_tap_user_authorized(mod.interactive.Tap(CHAT, "private", "", "")) is False  # no login, no auth


async def test_runner_auth_function_is_used(make_adapter):
    a = make_adapter()
    a._authorization_check = None
    seen = []

    class Runner:
        def _is_user_authorized(self, source):
            seen.append((source.user_id, source.chat_type))
            return source.user_id == "alice"

        async def handler(self, event):  # pragma: no cover
            return None

    a._message_handler = Runner().handler
    from gateway.platforms.event import MessageEvent  # noqa: F401

    class T:
        pass

    from importlib import import_module
    import sys as _sys

    inter = _sys.modules["yandex_messenger_plugin.interactive"]
    assert a._is_tap_user_authorized(inter.Tap(CHAT, "group", "alice", "A")) is True
    assert a._is_tap_user_authorized(inter.Tap(CHAT, "private", "bob", "B")) is False
    assert seen == [("alice", "group"), ("bob", "dm")]


async def test_i18n_russian(auth_alice, fake, monkeypatch):
    monkeypatch.setattr("agent.i18n.get_language", lambda: "ru")
    a = auth_alice()
    await a.send_slash_confirm(CHAT, "t", "m", "sess", "cid-ru")
    titles = [b["title"] for b in buttons_of(fake.last("messages/sendText")["json"])]
    assert titles == ["Подтвердить раз", "Всегда подтверждать", "Отмена"]
