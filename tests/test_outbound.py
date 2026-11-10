"""Outbound path: send/split, streaming edits, typing, media, notices, reactions."""
import inspect

import pytest
from aiohttp import web


def _prime(adapter, chat_id="0/0/dm-1", kind="private"):
    adapter._chat_types[chat_id] = kind
    return chat_id


async def test_send_plain_dm_has_no_reply_anchor(make_adapter, fake):
    a = make_adapter()
    chat = _prime(a)
    res = await a.send(chat, "hello **world** and *it*", reply_to="42")
    assert res.success and res.message_id
    body = fake.last("messages/sendText")["json"]
    assert body["chat_id"] == chat and body["text"] == "hello **world** and __it__"
    assert "reply_message_id" not in body  # private chat: no quoting by default
    assert not (await a.send(chat, "   ")).message_id  # whitespace-only is a no-op success


async def test_group_reply_anchor_only_on_first_chunk(make_adapter, fake):
    a = make_adapter()
    chat = _prime(a, "0/0/grp", "group")
    text = ("para " * 700 + "\n\n") * 3  # ~10.5k chars -> 2+ chunks
    res = await a.send(chat, text, reply_to="42")
    calls = fake.calls("messages/sendText")
    assert len(calls) >= 2 and res.success
    assert calls[0]["json"]["reply_message_id"] == 42
    assert all("reply_message_id" not in c["json"] for c in calls[1:])
    assert all(len(c["json"]["text"]) <= 6000 for c in calls)
    assert res.raw_response["message_ids"] and res.message_id == res.raw_response["message_ids"][0]


async def test_reply_mode_all_and_off(make_adapter, fake):
    a = make_adapter(reply_to_mode="all")
    chat = _prime(a, "0/0/grp", "group")
    await a.send(chat, ("x" * 5000 + "\n\n") * 2, reply_to="7")
    assert all(c["json"].get("reply_message_id") == 7 for c in fake.calls("messages/sendText"))
    fake.requests.clear()
    b = make_adapter(reply_to_mode="off")
    _prime(b, "0/0/grp", "group")
    await b.send("0/0/grp", "hi", reply_to="7")
    assert "reply_message_id" not in fake.last("messages/sendText")["json"]


async def test_reply_anchor_rejected_is_retried_without_it(make_adapter, fake):
    a = make_adapter()
    chat = _prime(a, "0/0/grp", "group")
    fake.responses["messages/sendText"] = [
        lambda r, rec: web.json_response({"ok": False, "description": "reply message not found"}, status=400),
        {"ok": True, "message_id": 9},
    ]
    res = await a.send(chat, "hi", reply_to="1")
    assert res.success and res.message_id == "9"
    assert "reply_message_id" not in fake.calls("messages/sendText")[-1]["json"]


async def test_send_rate_limited_result(make_adapter, fake):
    a = make_adapter()
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "Too Many Requests"}, status=429, headers={"Retry-After": "12"})
    res = await a.send("0/0/dm-1", "x")
    assert not res.success and res.retry_after == 12.0 and res.error_kind == "rate_limited" and res.retryable
    assert "retry after" in res.error.lower()  # the stream consumer detects flood by this wording


async def test_send_too_long_and_forbidden_kinds(make_adapter, fake):
    a = make_adapter()
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "Bot is not a member of the chat"}, status=403)
    res = await a.send("0/0/dm-1", "x")
    assert res.error_kind == "forbidden" and not res.retryable
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "text is too long"}, status=400)
    res = await a.send("0/0/dm-1", "x")
    assert res.error == "message_too_long" and res.error_kind == "too_long"


async def test_partial_send_reports_partial_overflow(make_adapter, fake):
    a = make_adapter()
    fake.responses["messages/sendText"] = [
        {"ok": True, "message_id": 1},
        lambda r, rec: web.json_response({"ok": False, "description": "nope"}, status=400),
    ]
    res = await a.send("0/0/dm-1", ("y" * 5500 + "\n\n") * 2)
    assert not res.success
    assert res.raw_response["partial_overflow"] and res.raw_response["last_message_id"] == "1"
    assert res.raw_response["delivered_chunks"] == 1


async def test_not_connected(make_adapter):
    a = make_adapter(connected=False)
    res = await a.send("0/0/dm-1", "x")
    assert not res.success and res.retryable
    assert (await a.edit_message("c", "1", "x")).success is False
    assert await a.delete_message("c", "1") is False


# ---------------------------------------------------------------- edits
async def test_edit_interim_is_skipped_while_slot_busy(make_adapter, fake):
    a = make_adapter(extra={"edit_budget_secs": 30})
    chat = _prime(a)
    first = await a.edit_message(chat, "5", "part 1")
    assert first.success and not first.raw_response
    body = fake.last("messages/sendText")["json"]
    assert body["message_id"] == 5 and body["text"] == "part 1" and "payload_id" not in body
    n = len(fake.calls("messages/sendText"))
    skipped = await a.edit_message(chat, "5", "part 1 and 2")
    assert skipped.success and skipped.raw_response == {"skipped": True}
    assert len(fake.calls("messages/sendText")) == n
    final = await a.edit_message(chat, "5", "part 1 and 2 done", finalize=True)  # never gated
    assert final.success and not final.raw_response
    assert fake.last("messages/sendText")["json"]["text"] == "part 1 and 2 done"


async def test_edit_interim_overflow_is_truncated_and_deduped(make_adapter, fake):
    a = make_adapter(extra={"edit_budget_secs": 0})
    chat = _prime(a)
    big = ("word " * 1500 + "\n\n") * 2  # > 6000
    r1 = await a.edit_message(chat, "5", big)
    assert r1.success and len(fake.last("messages/sendText")["json"]["text"]) <= 6000
    n = len(fake.calls("messages/sendText"))
    r2 = await a.edit_message(chat, "5", big + "more")  # same saturated preview -> no API call
    assert r2.success and len(fake.calls("messages/sendText")) == n


async def test_edit_finalize_overflow_splits(make_adapter, fake):
    a = make_adapter(extra={"edit_budget_secs": 0})
    chat = _prime(a)
    big = ("word " * 1500 + "\n\n") * 2
    res = await a.edit_message(chat, "5", big, finalize=True)
    calls = fake.calls("messages/sendText")
    assert res.success and len(calls) >= 2
    assert calls[0]["json"]["message_id"] == 5  # first chunk edits the preview
    assert all("message_id" not in c["json"] for c in calls[1:])  # the rest are fresh messages
    assert res.continuation_message_ids and res.message_id == res.continuation_message_ids[-1]


async def test_edit_finalize_overflow_partial_contract(make_adapter, fake):
    a = make_adapter(extra={"edit_budget_secs": 0})
    fake.responses["messages/sendText"] = [
        {"ok": True, "message_id": 5},
        lambda r, rec: web.json_response({"ok": False, "description": "boom"}, status=400),
    ]
    res = await a.edit_message("0/0/dm-1", "5", ("word " * 1500 + "\n\n") * 2, finalize=True)
    assert not res.success and res.raw_response["partial_overflow"] and res.error == "overflow_continuation_failed"


async def test_edit_flood_and_errors(make_adapter, fake):
    a = make_adapter(extra={"edit_budget_secs": 0})
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "Too Many Requests"}, status=429, headers={"Retry-After": "30"})
    res = await a.edit_message("0/0/dm-1", "5", "x")
    assert not res.success and res.retry_after == 30.0 and "retry after" in res.error.lower()
    # a finalize edit with a short penalty waits and retries once
    fake.requests.clear()
    fake.responses["messages/sendText"] = [
        lambda r, rec: web.json_response({"ok": False, "description": "Too Many Requests"}, status=429,
                                         headers={"Retry-After": "0.05"}),
        {"ok": True, "message_id": 5},
    ]
    res = await a.edit_message("0/0/dm-1", "5", "x", finalize=True)
    assert res.success and len(fake.calls("messages/sendText")) == 2
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "message not found"}, status=404)
    res = await a.edit_message("0/0/dm-1", "5", "x")
    assert not res.success and res.error_kind == "not_found"
    fake.responses["messages/sendText"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "message is not modified"}, status=400)
    assert (await a.edit_message("0/0/dm-1", "5", "x")).success


async def test_delete_message(make_adapter, fake):
    a = make_adapter()
    assert await a.delete_message("0/0/dm-1", "77") is True
    assert fake.last("messages/delete")["json"] == {"chat_id": "0/0/dm-1", "message_id": 77}
    fake.responses["messages/delete"] = lambda r, rec: web.json_response({"ok": False, "description": "x"}, status=404)
    assert await a.delete_message("0/0/dm-1", "77") is False


# ---------------------------------------------------------------- typing
async def test_typing_dm_status_vs_group_text(make_adapter, fake):
    a = make_adapter()
    dm, grp = _prime(a), _prime(a, "0/0/grp", "group")
    await a.send_typing(dm)
    assert fake.last("messages/sendTyping")["json"]["type"] == "text"
    a.set_status_text(dm, "Running tool: terminal " + "x" * 200)
    await a.send_typing(dm)
    body = fake.last("messages/sendTyping")["json"]
    assert body["type"] == "processing" and len(body["processing_content"]["text"]) == 100
    a.set_status_text(grp, "status")
    await a.send_typing(grp)  # processing indicators exist only in private chats
    assert fake.last("messages/sendTyping")["json"]["type"] == "text"
    a.set_status_text(dm, None)
    await a.send_typing(dm)
    assert fake.last("messages/sendTyping")["json"]["type"] == "text"


async def test_typing_backs_off_on_429(make_adapter, fake):
    a = make_adapter()
    fake.responses["messages/sendTyping"] = lambda r, rec: web.json_response(
        {"ok": False, "description": "Too Many Requests"}, status=429, headers={"Retry-After": "30"})
    await a.send_typing("0/0/dm-1")
    n = len(fake.calls("messages/sendTyping"))
    await a.send_typing("0/0/dm-1")
    assert len(fake.calls("messages/sendTyping")) == n  # cooldown: no hammering


# ---------------------------------------------------------------- media
def test_send_document_signature_matches_base(mod):
    """Base calls ``send_document(chat_id=, file_path=, caption=, file_name=, reply_to=, metadata=)``."""
    from gateway.platforms.base import BasePlatformAdapter

    ours = inspect.signature(mod.adapter.YandexMessengerAdapter.send_document).parameters
    base = inspect.signature(BasePlatformAdapter.send_document).parameters
    assert [n for n in base if n != "self"] == [n for n in ours if n != "self"]
    for name in ("send_image_file", "send_voice", "send_video", "send_image", "send_multiple_images"):
        theirs = inspect.signature(getattr(BasePlatformAdapter, name)).parameters
        mine = inspect.signature(getattr(mod.adapter.YandexMessengerAdapter, name)).parameters
        assert set(theirs) - {"self"} <= set(mine), name


async def test_send_document_and_caption(make_adapter, fake, tmp_path):
    a = make_adapter()
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4")
    res = await a.send_document(chat_id="0/0/dm-1", file_path=str(f), caption="Here you go", file_name="Q3.pdf",
                                reply_to="3", metadata=None)
    assert res.success
    fields = {n: (fn, data) for n, fn, data in fake.last("messages/sendFile")["fields"]}
    assert fields["document"] == ("Q3.pdf", b"%PDF-1.4") and fields["chat_id"][1] == b"0/0/dm-1"
    assert fake.last("messages/sendText")["json"]["text"] == "Here you go"  # the API has no caption field
    missing = await a.send_document("0/0/dm-1", str(tmp_path / "nope.pdf"))
    assert not missing.success and "not found" in missing.error


async def test_cache_prefix_is_stripped_from_sent_filename(make_adapter, fake, tmp_path, mod):
    a = make_adapter()
    cached = tmp_path / "doc_4eb5d4bed2bd_.env.example"
    cached.write_bytes(b"A=1")
    await a.send_document("0/0/dm-1", str(cached))
    assert fake.last("messages/sendFile")["fields"][-1][1] == ".env.example"
    explicit = await a.send_document("0/0/dm-1", str(cached), file_name="keep.txt")
    assert explicit.success and fake.last("messages/sendFile")["fields"][-1][1] == "keep.txt"
    f = mod.adapter.display_filename
    assert f("/x/doc_0123456789ab_report v2.pdf") == "report v2.pdf"
    assert f("/x/img_0123456789ab_shot.png") == "shot.png"
    assert f("/x/doc_0123456789ab_") == "doc_0123456789ab_"  # nothing left after the prefix: keep as is
    assert f("/x/document_final.pdf") == "document_final.pdf" and f("/x/doc_short_x.pdf") == "doc_short_x.pdf"


async def test_voice_video_and_image_file(make_adapter, fake, tmp_path):
    a = make_adapter()
    for name, method in (("v.ogg", "send_voice"), ("m.mp4", "send_video")):
        p = tmp_path / name
        p.write_bytes(b"data")
        arg = {"send_voice": "audio_path", "send_video": "video_path"}[method]
        res = await getattr(a, method)("0/0/dm-1", **{arg: str(p)}, caption=None)
        assert res.success
        assert fake.last("messages/sendFile")["fields"][-1][1] == name
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG")
    assert (await a.send_image_file("0/0/dm-1", str(img), caption="cap")).success
    assert fake.last("messages/sendImage")["fields"][-1][1] == "p.png"
    assert fake.last("messages/sendText")["json"]["text"] == "cap"


async def test_send_image_url_goes_through_guarded_downloader(make_adapter, fake, tmp_path, mod, monkeypatch):
    a = make_adapter()
    img = tmp_path / "dl.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    seen = []

    async def fake_cache(url, ext=".jpg", retries=2):
        seen.append((url, ext))
        return str(img)

    monkeypatch.setattr(mod.adapter, "cache_image_from_url", fake_cache)
    res = await a.send_image("0/0/dm-1", "https://example.com/pic.png?x=1", caption="c")
    assert res.success and seen == [("https://example.com/pic.png?x=1", ".png")]
    assert fake.last("messages/sendImage")["fields"][-1][1] == "dl.jpg"

    async def failing(url, ext=".jpg", retries=2):
        raise RuntimeError("blocked")

    monkeypatch.setattr(mod.adapter, "cache_image_from_url", failing)
    res = await a.send_image("0/0/dm-1", "http://10.0.0.1/x.png", caption="c")
    assert res.success  # text fallback
    assert fake.last("messages/sendText")["json"]["text"] == "c\nhttp://10.0.0.1/x.png"


async def test_send_multiple_images_albums(make_adapter, fake, tmp_path):
    a = make_adapter()
    urls = []
    for i in range(12):
        p = tmp_path / f"i{i}.png"
        p.write_bytes(b"\x89PNG")
        urls.append((p.as_uri(), f"alt{i}"))
    res = await a.send_multiple_images("0/0/dm-1", urls)
    assert res.success
    galleries = fake.calls("messages/sendGallery")
    assert [sum(1 for n, *_ in g["fields"] if n == "images") for g in galleries] == [10, 2]
    assert ("text", None, b"\n".join(f"alt{i}".encode() for i in range(10))) in galleries[0]["fields"]
    assert not (await a.send_multiple_images("0/0/dm-1", [])).success


# ---------------------------------------------------------------- notices, chat info
async def test_private_notice_targets_login(make_adapter, fake):
    a = make_adapter()
    chat = _prime(a, "0/0/grp", "group")
    res = await a.send_private_notice(chat, "bob", "*psst*")
    assert res.success
    body = fake.last("messages/sendText")["json"]
    assert body == {**body, "login": "bob", "text": "__psst__"} and "chat_id" not in body
    # group + no recipient: never leak into the group
    n = len(fake.calls("messages/sendText"))
    assert not (await a.send_private_notice(chat, None, "x")).success
    assert len(fake.calls("messages/sendText")) == n


async def test_private_notice_falls_back_to_private_chat(make_adapter, fake):
    a = make_adapter()
    chat = _prime(a)
    fake.responses["messages/sendText"] = [
        lambda r, rec: web.json_response({"ok": False, "description": "privacy"}, status=403),
        {"ok": True, "message_id": 3},
    ]
    res = await a.send_private_notice(chat, "alice", "hello")
    assert res.success and fake.calls("messages/sendText")[-1]["json"]["chat_id"] == chat


async def test_get_chat_info(make_adapter, fake):
    a = make_adapter()
    fake.responses["chats/getChat"] = {"ok": True, "data": {"type": "group", "id": "0/0/g", "name": "Dev team"}}
    info = await a.get_chat_info("0/0/g")
    assert info == {"name": "Dev team", "type": "group", "chat_id": "0/0/g"}
    assert fake.last("chats/getChat")["query"] == {"chat_id": "0/0/g"}
    fake.responses["chats/getChat"] = lambda r, rec: web.json_response({"ok": False, "description": "x"}, status=404)
    _prime(a, "0/0/dm-9")
    assert (await a.get_chat_info("0/0/dm-9"))["type"] == "dm"


# ---------------------------------------------------------------- reactions
async def test_reactions_use_only_names_the_chat_offers(make_adapter, fake):
    from gateway.platforms.event import ProcessingOutcome
    from gateway.platforms.event import MessageEvent

    a = make_adapter()
    chat = _prime(a)
    fake.responses["chats/getChat"] = {
        "ok": True, "data": {"type": "private", "id": chat,
                             "available_reactions": [{"type": "default_reaction", "name": "like"},
                                                     {"type": "default_reaction", "name": "eyes"}]}}
    src = a.build_source(chat_id=chat, chat_type="dm", user_id="alice")
    ev = MessageEvent(text="x", source=src, message_id="10")
    await a.on_processing_start(ev)
    assert fake.last("messages/sendReaction")["json"]["reaction"] == {"type": "default_reaction", "name": "eyes"}
    await a.on_processing_complete(ev, ProcessingOutcome.SUCCESS)
    reactions = [c["json"]["reaction"] for c in fake.calls("messages/sendReaction")]
    assert reactions[-2:] == [None, {"type": "default_reaction", "name": "like"}]  # remove, then ok
    n = len(fake.calls("messages/sendReaction"))
    await a.on_processing_complete(ev, ProcessingOutcome.FAILURE)  # "dislike" is not offered: only the removal
    assert [c["json"]["reaction"] for c in fake.calls("messages/sendReaction")][n:] == [None]


async def test_reactions_can_be_disabled_and_self_disable_on_rejection(make_adapter, fake):
    a = make_adapter(extra={"reactions": False})
    assert await a._add_reaction("0/0/dm-1", "10", "like") is False
    assert not fake.calls("messages/sendReaction")
    b = make_adapter()
    fake.responses["chats/getChat"] = {"ok": True, "data": {"available_reactions": []}}
    fake.responses["messages/sendReaction"] = lambda r, rec: web.json_response({"ok": False, "description": "bad"}, status=400)
    assert await b._add_reaction("0/0/dm-1", "10", "like") is False
    assert b._reactions_broken is False  # empty available list -> never even tried
    fake.responses["chats/getChat"] = {"ok": True, "data": {"available_reactions": [{"name": "like"}]}}
    b._reaction_names.clear()
    assert await b._add_reaction("0/0/dm-1", "10", "like") is False
    assert b._reactions_broken is True  # a server rejection stops further attempts this run
