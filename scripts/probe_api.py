#!/usr/bin/env python3
"""Empirical probe of the Yandex Messenger Bot API behaviours the plugin has to assume.

Needs a real bot token and a chat to talk in (use a private chat with yourself):

    export YANDEX_MESSENGER_BOT_TOKEN=...
    python scripts/probe_api.py --chat <chat_id | login | chat:<id>> [--wait 90] [--out docs/probe-output.json]

Steps (each is printed and stored in the JSON report; nothing is sent anywhere else):

  1. self/get                                 -> bot id / login / webhook_url
  2. chats/getChat                            -> chat type + available_reactions (the names the plugin may use)
  3. card with persist buttons                -> message_id
  4. edit card with an EMPTY keyboard         -> do the buttons disappear? (look at the chat; answer y/n)
  5. edit card with a NEW keyboard            -> are the buttons replaced in place? (y/n)
  6. sendTyping processing + text             -> accepted in private chats?
  7. sendReaction set + remove                -> which names work
  8. press a button in the chat               -> prints the raw bot_request update (message_id / chat / from)
     (the plugin does not rely on bot_request.message_id; this records what it really is)

The conclusions are meant to be copied into docs/API_FINDINGS.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "platforms" / "yandex_messenger" / "_vendor"))

from yandex_messenger_client import (  # noqa: E402
    Target,
    YMApiError,
    YandexMessengerClient,
    action_card,
    empty_keyboard,
)


def ask(question: str) -> str:
    try:
        return input(f"{question} [y/n/?] ").strip().lower() or "?"
    except EOFError:
        return "?"


async def attempt(report: dict, name: str, coro) -> object:
    try:
        result = await coro
        report[name] = {"ok": True, "result": result}
        print(f"  {name}: OK  {json.dumps(result, ensure_ascii=False)[:300]}")
        return result
    except YMApiError as exc:
        report[name] = {"ok": False, "error": str(exc), "status": exc.status, "kind": exc.kind}
        print(f"  {name}: FAILED  {exc}")
        return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chat", required=True)
    parser.add_argument("--wait", type=int, default=90, help="seconds to wait for a button press")
    parser.add_argument("--out", default=str(ROOT / "docs" / "probe-output.json"))
    args = parser.parse_args()
    token = os.environ.get("YANDEX_MESSENGER_BOT_TOKEN", "").strip()
    if not token:
        print("YANDEX_MESSENGER_BOT_TOKEN is not set", file=sys.stderr)
        return 2

    target = Target.parse(args.chat)
    report: dict = {}
    async with YandexMessengerClient(token) as client:
        print("1. self/get")
        await attempt(report, "self", client.get_self())

        print("2. chats/getChat")
        if target.chat_id:
            chat = await attempt(report, "get_chat", client.get_chat(target.chat_id))
            if chat:
                print("   available_reactions:", chat.get("available_reactions"))

        print("3. card with persist buttons")
        card = action_card([[("p1", "One", {"cb": "probe:1"}), ("p2", "Two", {"cb": "probe:2"})]])
        sent = await attempt(report, "send_card", client.send_text(target, "probe: card (buttons below)", suggest_buttons=card))
        message_id = (sent or {}).get("message_id")

        if message_id:
            print("4. edit with an empty keyboard")
            await attempt(report, "edit_empty_keyboard",
                          client.edit_text(target, message_id, "probe: edited, buttons should be GONE",
                                           suggest_buttons=empty_keyboard()))
            report["empty_keyboard_removes_buttons"] = ask("   Did the buttons disappear?")

            print("5. edit with a new keyboard")
            card2 = action_card([[("q1", "Three", {"cb": "probe:3"})]])
            await attempt(report, "edit_new_keyboard",
                          client.edit_text(target, message_id, "probe: edited, ONE new button", suggest_buttons=card2))
            report["new_keyboard_replaces_buttons"] = ask("   Is there exactly one button 'Three' now?")

        print("6. sendTyping processing + text")
        await attempt(report, "typing_processing",
                      client.send_typing(target, kind="processing", timeout=5, processing_text="Running a tool..."))

        print("7. reactions")
        if message_id:
            for name in ("eyes", "like", "dislike", "fire"):
                await attempt(report, f"reaction_{name}", client.send_reaction(target, message_id, name))
            await attempt(report, "reaction_remove", client.send_reaction(target, message_id, None))

        print(f"8. press a button within {args.wait}s (a fresh card is sent now)")
        card3 = action_card([[("t1", "PRESS ME", {"cb": "probe:press"})]])
        await attempt(report, "send_press_card", client.send_text(target, "probe: press the button", suggest_buttons=card3))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + args.wait
        offset = None
        while loop.time() < deadline:
            updates = await client.get_updates(offset=offset, limit=20)
            for update in updates:
                offset = max(offset or 0, update.get("update_id", 0) + 1)
                if "bot_request" in update:
                    report["bot_request_update"] = update
                    print("  bot_request update:", json.dumps(update, ensure_ascii=False, indent=2))
                    loop = None
                    break
            if loop is None:
                break
            await asyncio.sleep(1)
        else:
            print("  no button press seen")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
