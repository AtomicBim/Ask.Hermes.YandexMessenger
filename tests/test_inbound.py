"""Inbound path: filtering, group gating, media, dedup, forwards/replies."""
import pytest
from aiohttp import web


async def test_dm_text_builds_event(make_adapter, make_update):
    a = make_adapter()
    await a._dispatch_update(make_update("hi there", message_id=7))
    (ev,) = a.events
    assert ev.text == "hi there" and ev.message_id == "7"
    assert ev.source.chat_id == "0/0/dm-1" and ev.source.chat_type == "dm"
    assert ev.source.user_id == "alice" and ev.source.user_name == "Alice"
    assert ev.message_type.value == "text"
    assert a._is_private("0/0/dm-1")


async def test_command_type(make_adapter, make_update):
    a = make_adapter()
    await a._dispatch_update(make_update("/model gpt"))
    assert a.events[0].message_type.value == "command"


async def test_dedup_on_update_id(make_adapter, make_update):
    a = make_adapter()
    u = make_update("x", update_id=55)
    await a._dispatch_update(u)
    await a._dispatch_update(dict(u))
    assert len(a.events) == 1


async def test_robot_and_self_are_ignored(make_adapter, make_update):
    a = make_adapter()
    robot = make_update("beep")
    robot["from"]["robot"] = True
    await a._dispatch_update(robot)
    me = make_update("echo", update_id=2, login="hermes_bot")
    await a._dispatch_update(me)
    by_id = make_update("echo2", update_id=3)
    by_id["from"]["id"] = "bot-guid"
    await a._dispatch_update(by_id)
    assert a.events == []


async def test_ignored_event_kinds(make_adapter, make_update):
    a = make_adapter()
    await a._dispatch_update(make_update(None, update_id=1, reaction={"message_id": 1, "reaction": {}, "action": "add"}))
    await a._dispatch_update(make_update(None, update_id=2, chat_members_update={"new_chat_members": []}))
    await a._dispatch_update(make_update("post", update_id=3, chat_type="channel", chat_id="0/0/chan"))
    assert a.events == []


async def test_group_requires_mention(make_adapter, make_update):
    a = make_adapter()
    g = dict(chat_type="group", chat_id="0/0/grp")
    await a._dispatch_update(make_update("just chatting", update_id=1, **g))
    assert a.events == []
    await a._dispatch_update(make_update("@hermes_bot what is 2+2?", update_id=2, **g))
    assert a.events[-1].text == "what is 2+2?" and a.events[-1].source.chat_type == "group"
    await a._dispatch_update(make_update("hey @HERMES_BOT, status", update_id=3, **g))
    assert a.events[-1].text == "hey , status".replace(" ,", ",")
    await a._dispatch_update(make_update("/help", update_id=4, **g))
    assert a.events[-1].text == "/help"
    await a._dispatch_update(make_update("/model@hermes_bot gpt", update_id=5, **g))
    assert a.events[-1].text == "/model gpt"
    await a._dispatch_update(make_update("email me at x@hermes_bot.example", update_id=6, **g))
    assert len(a.events) == 4  # a look-alike inside an address is not a mention


async def test_group_reply_to_bot_counts_as_mention(make_adapter, make_update):
    a = make_adapter()
    replied = {"message_id": 3, "text": "earlier answer", "from": {"login": "hermes_bot", "robot": True, "id": "bot-guid"}}
    await a._dispatch_update(make_update("and then?", update_id=1, chat_type="group", chat_id="0/0/g",
                                         reply_to_message=replied))
    (ev,) = a.events
    assert ev.reply_to_is_own_message and ev.reply_to_text == "earlier answer" and ev.reply_to_message_id == "3"


async def test_group_without_require_mention(make_adapter, make_update):
    a = make_adapter(extra={"require_mention": False})
    await a._dispatch_update(make_update("free chat", chat_type="group", chat_id="0/0/g"))
    assert len(a.events) == 1


async def test_allowed_chats_filter(make_adapter, make_update):
    a = make_adapter(extra={"allowed_chats": "0/0/ok", "require_mention": False})
    await a._dispatch_update(make_update("a", update_id=1, chat_type="group", chat_id="0/0/nope"))
    await a._dispatch_update(make_update("b", update_id=2, chat_type="group", chat_id="0/0/ok"))
    assert [e.text for e in a.events] == ["b"]
    # DMs are not subject to the chat allowlist (user authorization is the gateway's job)
    await a._dispatch_update(make_update("c", update_id=3))
    assert [e.text for e in a.events] == ["b", "c"]


async def test_file_download_and_classification(make_adapter, make_update, fake):
    a = make_adapter()
    fake.responses["messages/getFile"] = lambda r, rec: web.Response(body=b"col1,col2\n1,2\n")
    await a._dispatch_update(make_update("see file", file={"id": "f1", "name": "data.csv", "size": 14}))
    (ev,) = a.events
    assert ev.message_type.value == "document" and len(ev.media_urls) == 1
    assert ev.media_urls[0].endswith("data.csv") and ev.media_types[0].startswith("text/")
    assert "[Content of data.csv]" in ev.text and ev.media_text_inlined == [True]
    assert fake.last("messages/getFile")["json"] == {"file_id": "f1"}


async def test_audio_is_voice_and_binary_document(make_adapter, make_update, fake):
    a = make_adapter()
    fake.responses["messages/getFile"] = lambda r, rec: web.Response(body=b"OggS\x00\x02" + b"\x00" * 64)
    await a._dispatch_update(make_update(None, update_id=1, file={"id": "v1", "name": "voice.ogg", "size": 70}))
    ev = a.events[-1]
    assert ev.message_type.value == "voice" and ev.media_urls[0].endswith(".ogg")
    fake.responses["messages/getFile"] = lambda r, rec: web.Response(body=b"PK\x03\x04binary")
    await a._dispatch_update(make_update(None, update_id=2, file={"id": "z1", "name": "bundle.zip", "size": 12}))
    ev = a.events[-1]
    assert ev.message_type.value == "document" and ev.media_urls[0].endswith("bundle.zip")
    assert ev.media_text_inlined == [False]


async def test_oversize_file_gets_polite_reply(make_adapter, make_update, fake):
    a = make_adapter(extra={"max_file_mb": 1})
    await a._dispatch_update(make_update("big", file={"id": "f9", "name": "huge.bin", "size": 5 * 1024 * 1024}))
    assert not fake.calls("messages/getFile")  # declared size short-circuits the download
    sent = fake.last("messages/sendText")["json"]["text"]
    assert "huge.bin" in sent and "слишком большой" in sent
    (ev,) = a.events
    assert ev.media_urls == [] and "skipped" in ev.text and ev.text.endswith("big")


async def test_images_pick_originals(make_adapter, make_update, fake):
    a = make_adapter()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"IEND\xaeB`\x82"
    fake.responses["messages/getFile"] = lambda r, rec: web.Response(body=png)
    images = [
        [{"file_id": "a?size=s", "width": 10, "height": 10},
         {"file_id": "a", "width": 800, "height": 600, "size": 100, "name": "one.png"}],
        [{"file_id": "b?size=s", "width": 10, "height": 10},
         {"file_id": "b", "width": 640, "height": 480, "size": 90, "name": "two.png"}],
    ]
    await a._dispatch_update(make_update(None, images=images))
    ids = [c["json"]["file_id"] for c in fake.calls("messages/getFile")]
    assert ids == ["a", "b"]  # every picture, original variant only
    ev = a.events[0]
    assert ev.message_type.value == "photo" and len(ev.media_urls) == 2


async def test_forwarded_messages_are_appended(make_adapter, make_update):
    a = make_adapter()
    fwd = [{"message_id": 1, "text": "original words", "from": {"login": "bob", "display_name": "Bob"}}]
    await a._dispatch_update(make_update("please summarise", forwarded_messages=fwd))
    text = a.events[0].text
    assert "please summarise" in text and "[Forwarded from Bob]" in text and "original words" in text


async def test_sticker_marker(make_adapter, make_update):
    a = make_adapter()
    await a._dispatch_update(make_update(None, sticker={"id": "s", "set_id": "set"}))
    assert a.events[0].text == "[sticker]"


async def test_bot_request_does_not_become_a_message(make_adapter, make_update):
    a = make_adapter()
    seen = []

    async def fake_handler(update):
        seen.append(update["bot_request"]["server_action"]["payload"])

    a._handle_bot_request = fake_handler
    upd = make_update(None, bot_request={"server_action": {"name": "hermes", "payload": {"cb": "mx"}}})
    await a._dispatch_update(upd)
    import asyncio

    await asyncio.sleep(0.01)
    assert seen == [{"cb": "mx"}] and a.events == []
