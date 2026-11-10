"""Interactive prompts for the Yandex Messenger adapter (``InteractiveMixin``).

A port of the Telegram adapter's button flows onto ``suggest_buttons``:

* every button carries ``server_action{name="hermes", payload={"cb": "<callback string>"}}``
  plus ``set_elements_state`` that disables the whole card while the bot works;
* callback strings are 1:1 with Telegram (``ea:`` ``cl:`` ``sc:`` ``update_prompt:`` ``mp:`` ``mpg:``
  ``mpv:`` ``mm:`` ``mg:`` ``mc:`` ``mb`` ``mx`` ``cp:``) so the dispatcher logic is the same;
* the Bot API has no ``answerCallbackQuery``: feedback is the card edit; refusals and expiries go
  to the tapping user as a private notice and never touch the shared card.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from gateway.platforms.base import ExecApprovalPrompt, SendResult

from ._vendor.yandex_messenger_client import (
    YMApiError,
    action_card,
    empty_keyboard,
    to_yandex_markdown,
)

logger = logging.getLogger(__name__)

CALLBACK_ACTION = "hermes"
MAX_TEXT = 6000
_CARD_CAP = 500
PICKER_DISABLE_SECS = 15
APPROVAL_CHOICES = frozenset({"once", "session", "always", "deny"})

# --------------------------------------------------------------------------- i18n
_STRINGS = {
    "en": {
        "unauthorized": "⛔ You are not authorized to use this button.",
        "already_resolved": "This prompt was already resolved or has expired.",
        "invalid": "Invalid button data.",
        "resolved_once": "✅ Allowed once",
        "resolved_session": "✅ Allowed for this session",
        "resolved_always": "✅ Always allowed",
        "resolved_deny": "❌ Denied",
        "resolved_generic": "Done",
        "approval_expired": "⌛ Approval expired — the command was not run.",
        "resolved_by": "{label} — {user}",
        "sc_once": "✅ Approved once",
        "sc_always": "✅ Always approved",
        "sc_cancel": "🚫 Cancelled",
        "sc_btn_once": "Approve once",
        "sc_btn_always": "Always approve",
        "sc_btn_cancel": "Cancel",
        "btn_other": "✏️ Other (type answer)",
        "awaiting_typed": "{user} chose to type an answer — send it as your next message.",
        "clarify_expired": "⌛ This question expired; the answer was not delivered.",
        "invalid_choice": "Invalid choice.",
        "update_header": "☤ Hermes update",
        "update_default": " (default: {default})",
        "update_yes": "Yes",
        "update_no": "No",
        "update_answered": "Update prompt answered: {answer}",
        "picker_title": "⚙ Model picker",
        "current_model": "Current model: {model}",
        "provider_label": "Provider: {provider}",
        "select_provider": "Select a provider:",
        "select_model": "Select a model:",
        "provider_family": "Provider family: {family}",
        "more_available": "{count} more available",
        "unknown_model": "unknown",
        "prev": "◀ Prev",
        "next": "Next ▶",
        "back": "◀ Back",
        "cancel": "✖ Cancel",
        "cancelled": "Selection cancelled.",
        "picker_expired": "This picker has expired — run the command again.",
        "invalid_selection": "Invalid selection.",
        "switch_anyway": "Switch anyway",
        "switch_error": "Model switch failed: {error}",
        "apply_error": "Could not apply selection: {error}",
        "provider_not_found": "Provider not found.",
        "no_choices": "No choices",
        "user_fallback": "user",
    },
    "ru": {
        "unauthorized": "⛔ У вас нет прав на это действие.",
        "already_resolved": "Этот запрос уже обработан или устарел.",
        "invalid": "Некорректные данные кнопки.",
        "resolved_once": "✅ Разрешено один раз",
        "resolved_session": "✅ Разрешено на сессию",
        "resolved_always": "✅ Разрешено навсегда",
        "resolved_deny": "❌ Отклонено",
        "resolved_generic": "Готово",
        "approval_expired": "⌛ Время подтверждения истекло — команда не выполнена.",
        "resolved_by": "{label} — {user}",
        "sc_once": "✅ Подтверждено один раз",
        "sc_always": "✅ Подтверждено навсегда",
        "sc_cancel": "🚫 Отменено",
        "sc_btn_once": "Подтвердить раз",
        "sc_btn_always": "Всегда подтверждать",
        "sc_btn_cancel": "Отмена",
        "btn_other": "✏️ Другое (ввести текст)",
        "awaiting_typed": "{user} выбрал(а) ввести ответ — отправьте его следующим сообщением.",
        "clarify_expired": "⌛ Вопрос устарел; ответ не доставлен.",
        "invalid_choice": "Некорректный выбор.",
        "update_header": "☤ Обновление Hermes",
        "update_default": " (по умолчанию: {default})",
        "update_yes": "Да",
        "update_no": "Нет",
        "update_answered": "Ответ на запрос обновления: {answer}",
        "picker_title": "⚙ Выбор модели",
        "current_model": "Текущая модель: {model}",
        "provider_label": "Провайдер: {provider}",
        "select_provider": "Выберите провайдера:",
        "select_model": "Выберите модель:",
        "provider_family": "Семейство провайдеров: {family}",
        "more_available": "ещё доступно: {count}",
        "unknown_model": "неизвестно",
        "prev": "◀ Назад",
        "next": "Вперёд ▶",
        "back": "◀ К списку",
        "cancel": "✖ Отмена",
        "cancelled": "Выбор отменён.",
        "picker_expired": "Выбор устарел — повторите команду.",
        "invalid_selection": "Некорректный выбор.",
        "switch_anyway": "Всё равно переключить",
        "switch_error": "Не удалось сменить модель: {error}",
        "apply_error": "Не удалось применить выбор: {error}",
        "provider_not_found": "Провайдер не найден.",
        "no_choices": "Нет вариантов",
        "user_fallback": "пользователь",
    },
}


def _lang() -> str:
    try:
        from agent.i18n import get_language

        return "ru" if str(get_language() or "").lower().startswith("ru") else "en"
    except Exception:
        return "en"


def tr(key: str, **kwargs: Any) -> str:
    table = _STRINGS[_lang()]
    text = table.get(key) or _STRINGS["en"].get(key) or key
    return text.format(**kwargs) if kwargs else text


@dataclass
class Tap:
    """Context of one button press."""

    chat_id: str
    chat_type: str
    login: str
    name: str
    thread_id: Optional[str] = None

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def gateway_chat_type(self) -> str:
        return {"private": "dm"}.get(self.chat_type, self.chat_type or "dm")


def _rows_of_two(items: list) -> list:
    return [items[i:i + 2] for i in range(0, len(items), 2)]


class InteractiveMixin:
    """Interactive UX; mixed into ``YandexMessengerAdapter`` (needs ``_client``, ``_target``,
    ``_error_result``, ``send``, ``send_private_notice``, ``resume_typing_for_chat``)."""

    _PROVIDER_PAGE_SIZE = 10
    _MODEL_PAGE_SIZE = 8

    # ------------------------------------------------------------------ state
    def _init_interactive_state(self) -> None:
        self._approval_counter = itertools.count(1)
        self._approval_state: dict = {}          # approval_id -> session_key
        self._approval_request_ids: dict = {}    # approval_id -> tools.approval request_id
        self._slash_confirm_state: dict = {}     # confirm_id -> session_key
        self._clarify_state: dict = {}           # clarify_id -> session_key
        self._model_picker_state: dict = {}      # chat_id -> picker state
        self._choice_picker_state: dict = {}     # chat_id -> picker state
        self._cards: dict = {}                   # (kind, id) -> {chat_id, message_id, text, choices}
        self._card_seq = itertools.count(1)

    def _remember_card(self, key: tuple, **data: Any) -> None:
        self._cards[key] = data
        while len(self._cards) > _CARD_CAP:
            self._cards.pop(next(iter(self._cards)), None)

    # ------------------------------------------------------------------ card plumbing
    @staticmethod
    def _fit(text: str) -> str:
        return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"

    def _card_from_specs(self, rows: list, *, timeout: int = 60) -> dict:
        """``rows`` = ``[[(title, callback_string), ...], ...]`` -> ``suggest_buttons`` dict."""
        seq = next(self._card_seq)
        counter = itertools.count(1)
        spec_rows = [
            [(f"b{seq}_{next(counter)}", title, {"cb": cb}) for (title, cb) in row] for row in rows if row
        ]
        return action_card(spec_rows, name=CALLBACK_ACTION, persist=True, disable_timeout=timeout)

    async def _post_card(
        self, chat_id: str, text: str, card: Optional[dict], metadata: Optional[dict] = None,
        *, important: bool = False,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            result = await self._client.send_text(
                self._target(chat_id),
                self._fit(to_yandex_markdown(text)),
                suggest_buttons=card,
                important=True if important else None,
                thread_id=self._thread_id(metadata),
            )
        except YMApiError as exc:
            return self._error_result(exc)
        return SendResult(success=True, message_id=str(result.get("message_id")), raw_response=result)

    async def _edit_card(self, chat_id: str, message_id: Any, text: str, card: Optional[dict]) -> bool:
        if not self._client or message_id in (None, ""):
            return False
        try:
            await self._client.edit_text(
                self._target(chat_id), int(message_id), self._fit(to_yandex_markdown(text)), suggest_buttons=card
            )
            return True
        except (YMApiError, ValueError) as exc:
            logger.debug("[%s] card edit failed: %s", self.name, exc)
            return False

    async def _finish_card(self, chat_id: str, message_id: Any, text: str) -> bool:
        """Replace a card with plain text and try to drop its buttons.

        First try an explicit empty keyboard; if the API rejects that, edit the text alone.
        """
        if await self._edit_card(chat_id, message_id, text, empty_keyboard()):
            return True
        return await self._edit_card(chat_id, message_id, text, None)

    async def _replace_card(self, chat_id: str, state: dict, text: str, card: dict) -> None:
        """Re-render a picker card in place; if editing fails, delete + resend and follow the new id."""
        if await self._edit_card(chat_id, state.get("msg_id"), text, card):
            return
        with contextlib.suppress(Exception):
            await self._client.delete_message(self._target(chat_id), int(state["msg_id"]))
        result = await self._post_card(chat_id, text, card)
        if result.success and result.message_id:
            state["msg_id"] = result.message_id

    # ------------------------------------------------------------------ authorization
    @staticmethod
    def _env_allowlist_decision(login: str) -> Optional[bool]:
        from gateway.platforms._shared import platform_gate_env

        allowed = platform_gate_env("YANDEX_MESSENGER_ALLOWED_USERS")
        if not allowed:
            return None
        ids = {item.strip().lower() for item in allowed.split(",") if item.strip()}
        return "*" in ids or login.lower() in ids

    def _legacy_runner_auth_fn(self):
        runner = getattr(getattr(self, "_message_handler", None), "__self__", None)
        fn = getattr(runner, "_is_user_authorized", None)
        return fn if callable(fn) else None

    def _is_tap_user_authorized(self, tap: Tap) -> bool:
        """Who may press buttons: the injected runner check, the runner's own auth, then the env
        allowlist; with nothing configured the answer is DENY (fail closed)."""
        login = (tap.login or "").strip()
        if not login:
            return False
        if getattr(self, "_authorization_check", None) is not None:
            injected = self._is_sender_authorized(login, chat_type=tap.gateway_chat_type, chat_id=tap.chat_id)
            if injected is not None:
                return injected
        auth_fn = self._legacy_runner_auth_fn()
        if auth_fn is not None:
            try:
                from gateway.session import SessionSource

                from .gateway_env import platform as _platform

                source = SessionSource(
                    platform=_platform(), chat_id=tap.chat_id,
                    chat_type=tap.gateway_chat_type, user_id=login, user_name=tap.name or None,
                )
                return bool(auth_fn(source))
            except Exception:
                logger.debug("[%s] runner auth fallback failed for %s", self.name, login, exc_info=True)
        decision = self._env_allowlist_decision(login)
        if decision is None:
            from gateway.platforms._shared import platform_gate_env

            return any(
                platform_gate_env(var).lower() in {"1", "true", "yes", "on"}
                for var in ("YANDEX_MESSENGER_ALLOW_ALL_USERS", "GATEWAY_ALLOW_ALL_USERS")
            )
        return decision

    async def _notify_tap(self, tap: Tap, text: str) -> None:
        """Tell the tapping user (privately) why nothing happened; never edits the shared card."""
        try:
            await self.send_private_notice(tap.chat_id, tap.login or None, text)
        except Exception:
            logger.debug("[%s] tap notice failed", self.name, exc_info=True)

    async def _tap_authorized(self, tap: Tap) -> bool:
        if self._is_tap_user_authorized(tap):
            return True
        await self._notify_tap(tap, tr("unauthorized"))
        return False

    # ------------------------------------------------------------------ approval prompt
    async def _send_exec_approval_prompt(self, prompt: ExecApprovalPrompt) -> SendResult:
        approval_id = next(self._approval_counter)
        buttons = [(label, f"ea:{choice}:{approval_id}") for label, choice, _style in prompt.actions]
        card = self._card_from_specs(_rows_of_two(buttons))
        result = await self._post_card(prompt.chat_id, prompt.text, card, prompt.metadata, important=True)
        if result.success:
            self._approval_state[approval_id] = prompt.session_key
            self._approval_request_ids[approval_id] = prompt.request_id
            self._remember_card(
                ("ea", approval_id), chat_id=prompt.chat_id, message_id=result.message_id,
                text=prompt.text, choices=frozenset(prompt.choices),
            )
        return result

    async def _on_approval_tap(self, tap: Tap, cb: str) -> None:
        parts = cb.split(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            await self._notify_tap(tap, tr("invalid"))
            return
        choice, approval_id = parts[1], int(parts[2])
        if not await self._tap_authorized(tap):
            return
        card = self._cards.get(("ea", approval_id)) or {}
        session_key = self._approval_state.pop(approval_id, None)
        if not session_key:
            await self._notify_tap(tap, tr("already_resolved"))
            return
        allowed = card.get("choices") or APPROVAL_CHOICES
        if choice not in allowed:
            self._approval_state[approval_id] = session_key  # a forged choice must not burn the prompt
            await self._notify_tap(tap, tr("invalid"))
            return
        request_id = self._approval_request_ids.pop(approval_id, None)
        count = 0
        try:
            from tools.approval import resolve_gateway_approval

            count = resolve_gateway_approval(session_key, choice, request_id=request_id)
            logger.info("[%s] button resolved %d approval(s) (choice=%s, user=%s)", self.name, count, choice, tap.login)
        except Exception as exc:
            logger.error("[%s] resolving approval from button failed: %s", self.name, exc)
        if count:
            label = tr(f"resolved_{choice}") if f"resolved_{choice}" in _STRINGS["en"] else tr("resolved_generic")
            line = tr("resolved_by", label=label, user=tap.name or tap.login)
        else:
            line = tr("approval_expired")
        await self._finish_card(card.get("chat_id", tap.chat_id), card.get("message_id"), f"{card.get('text', '')}\n\n{line}")
        if count:
            self.resume_typing_for_chat(tap.chat_id)

    # ------------------------------------------------------------------ slash confirm
    async def send_slash_confirm(
        self, chat_id: str, title: str, message: str, session_key: str, confirm_id: str,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        card = self._card_from_specs([
            [(tr("sc_btn_once"), f"sc:once:{confirm_id}"), (tr("sc_btn_always"), f"sc:always:{confirm_id}")],
            [(tr("sc_btn_cancel"), f"sc:cancel:{confirm_id}")],
        ])
        text = f"**{title}**\n\n{message}" if title else message
        result = await self._post_card(chat_id, text, card, metadata)
        if result.success:
            self._slash_confirm_state[confirm_id] = session_key
            self._remember_card(("sc", confirm_id), chat_id=chat_id, message_id=result.message_id, text=text)
        return result

    async def _on_slash_confirm_tap(self, tap: Tap, cb: str) -> None:
        parts = cb.split(":", 2)
        if len(parts) != 3 or parts[1] not in {"once", "always", "cancel"}:
            await self._notify_tap(tap, tr("invalid"))
            return
        choice, confirm_id = parts[1], parts[2]
        if not await self._tap_authorized(tap):
            return
        session_key = self._slash_confirm_state.pop(confirm_id, None)
        if not session_key:
            await self._notify_tap(tap, tr("already_resolved"))
            return
        card = self._cards.get(("sc", confirm_id)) or {}
        label = tr({"once": "sc_once", "always": "sc_always", "cancel": "sc_cancel"}[choice])
        line = tr("resolved_by", label=label, user=tap.name or tap.login)
        await self._finish_card(card.get("chat_id", tap.chat_id), card.get("message_id"), f"{card.get('text', '')}\n\n{line}")
        try:
            from tools import slash_confirm as _slash_confirm

            result_text = await _slash_confirm.resolve(session_key, confirm_id, choice)
            if result_text:
                await self.send(tap.chat_id, result_text, metadata=self._tap_metadata(tap))
        except Exception as exc:
            logger.error("[%s] slash-confirm callback failed: %s", self.name, exc, exc_info=True)

    # ------------------------------------------------------------------ clarify
    async def send_clarify(
        self, chat_id: str, question: str, choices: Optional[list], clarify_id: str, session_key: str,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        text = f"❓ {question}"
        card = None
        if choices:
            text += "\n\n" + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(choices))
            numbered = [(str(i + 1), f"cl:{clarify_id}:{i}") for i in range(len(choices))]
            rows = [numbered[i:i + 5] for i in range(0, len(numbered), 5)]
            rows.append([(tr("btn_other"), f"cl:{clarify_id}:other")])
            card = self._card_from_specs(rows)
        result = await self._post_card(chat_id, text, card, metadata)
        if result.success:
            self._clarify_state[clarify_id] = session_key
            self._remember_card(("cl", clarify_id), chat_id=chat_id, message_id=result.message_id, text=text)
        return result

    async def _on_clarify_tap(self, tap: Tap, cb: str) -> None:
        parts = cb.split(":", 2)
        if len(parts) != 3:
            await self._notify_tap(tap, tr("invalid"))
            return
        clarify_id, token = parts[1], parts[2]
        if not await self._tap_authorized(tap):
            return
        if clarify_id not in self._clarify_state:
            await self._notify_tap(tap, tr("already_resolved"))
            return
        card = self._cards.get(("cl", clarify_id)) or {}
        chat_id, message_id, base = card.get("chat_id", tap.chat_id), card.get("message_id"), card.get("text", "")
        user = tap.name or tap.login or tr("user_fallback")
        if token == "other":
            flipped = False
            try:
                from tools.clarify_gateway import mark_awaiting_text

                flipped = mark_awaiting_text(clarify_id)
            except Exception as exc:
                logger.warning("[%s] mark_awaiting_text failed: %s", self.name, exc)
            if not flipped:
                self._clarify_state.pop(clarify_id, None)
                await self._finish_card(chat_id, message_id, f"{base}\n\n{tr('clarify_expired')}")
                return
            await self._finish_card(chat_id, message_id, f"{base}\n\n__{tr('awaiting_typed', user=user)}__")
            return
        try:
            idx = int(token)
        except ValueError:
            await self._notify_tap(tap, tr("invalid_choice"))
            return
        resolved_text = None
        try:
            from tools.clarify_gateway import get_pending

            entry = get_pending(clarify_id)
            if entry and entry.choices and 0 <= idx < len(entry.choices):
                resolved_text = entry.choices[idx]
        except Exception:
            resolved_text = None
        if resolved_text is None:
            resolved_text = f"choice {idx + 1}"  # race with a timeout: still an intentional answer
        self._clarify_state.pop(clarify_id, None)
        try:
            from tools.clarify_gateway import resolve_gateway_clarify

            resolved = resolve_gateway_clarify(clarify_id, resolved_text)
        except Exception as exc:
            logger.error("[%s] resolve_gateway_clarify failed: %s", self.name, exc)
            resolved = False
        if resolved:
            await self._finish_card(chat_id, message_id, f"{base}\n\n**{user}:** {resolved_text}")
        else:
            await self._finish_card(chat_id, message_id, f"{base}\n\n{tr('clarify_expired')}")

    # ------------------------------------------------------------------ update prompt
    async def send_update_prompt(
        self, chat_id: str, prompt: str, default: str = "", session_key: str = "",
        metadata: Optional[dict] = None,
    ) -> SendResult:
        hint = tr("update_default", default=default) if default else ""
        text = f"**{tr('update_header')}**\n\n{prompt}{hint}"
        card = self._card_from_specs([[(tr("update_yes"), "update_prompt:y"), (tr("update_no"), "update_prompt:n")]])
        result = await self._post_card(chat_id, text, card, metadata)
        if result.success:
            self._remember_card(("up", str(chat_id)), chat_id=chat_id, message_id=result.message_id, text=text)
        return result

    async def _on_update_prompt_tap(self, tap: Tap, cb: str) -> None:
        answer = cb.split(":", 1)[1] if ":" in cb else ""
        if answer not in {"y", "n"}:
            await self._notify_tap(tap, tr("invalid"))
            return
        if not await self._tap_authorized(tap):
            return
        card = self._cards.pop(("up", tap.chat_id), None) or {}
        word = tr("update_yes") if answer == "y" else tr("update_no")
        await self._finish_card(
            card.get("chat_id", tap.chat_id), card.get("message_id"),
            f"{card.get('text', '')}\n\n{tr('update_answered', answer=f'**{word}**')}",
        )
        try:
            from hermes_constants import get_hermes_home

            path = get_hermes_home() / ".update_response"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(answer, encoding="utf-8")
            os.replace(tmp, path)
            logger.info("[%s] update prompt answered '%s' by %s", self.name, answer, tap.login)
        except Exception as exc:
            logger.error("[%s] writing update response failed: %s", self.name, exc)

    # ------------------------------------------------------------------ choice picker
    async def send_choice_picker(
        self, chat_id: str, title: str, choices: list, session_key: str, on_choice_selected,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        buttons = []
        for i, choice in enumerate(choices):
            label = str(choice.get("label") or choice.get("value") or "")
            if choice.get("is_current"):
                label = f"✓ {label}"
            buttons.append((label, f"cp:{i}"))
        if not buttons:
            return SendResult(success=False, error=tr("no_choices"))
        card = self._card_from_specs(_rows_of_two(buttons), timeout=PICKER_DISABLE_SECS)
        result = await self._post_card(chat_id, title, card, metadata)
        if result.success:
            self._choice_picker_state[str(chat_id)] = {
                "msg_id": result.message_id, "choices": choices, "session_key": session_key,
                "on_choice_selected": on_choice_selected, "text": title,
            }
        return result

    async def _finish_picker(self, chat_id: str, state: dict, text: str) -> None:
        if not await self._finish_card(chat_id, state.get("msg_id"), text):
            with contextlib.suppress(Exception):
                await self.send(chat_id, text)

    async def _on_choice_picker_tap(self, tap: Tap, cb: str) -> None:
        chat_id = tap.chat_id
        state = self._choice_picker_state.get(chat_id)
        if not state:
            await self._notify_tap(tap, tr("picker_expired"))
            return
        try:
            choice = state["choices"][int(cb[3:])]
        except (ValueError, IndexError):
            await self._notify_tap(tap, tr("invalid_selection"))
            return
        callback = state.get("on_choice_selected")
        if not callback:
            await self._notify_tap(tap, tr("picker_expired"))
            return
        self._choice_picker_state.pop(chat_id, None)  # claim: a double tap must not apply twice
        try:
            result_text = await callback(chat_id, str(choice.get("value") or ""))
        except Exception as exc:
            logger.error("[%s] choice picker selection failed: %s", self.name, exc)
            result_text = tr("apply_error", error=str(exc))
        await self._finish_picker(chat_id, state, str(result_text or ""))

    # ------------------------------------------------------------------ model picker
    @staticmethod
    def _provider_label(slug: str) -> str:
        try:
            from hermes_cli.providers import get_label

            return get_label(slug)
        except Exception:
            return slug

    @staticmethod
    def _provider_button(p: dict) -> tuple:
        count = p.get("total_models", len(p.get("models", [])))
        label = f"{p['name']} ({count})"
        if p.get("is_current"):
            label = f"✓ {label}"
        return (label, f"mp:{p['slug']}")

    def _nav_row(self, page: int, total_pages: int, prefix: str) -> list:
        nav = []
        if page > 0:
            nav.append((tr("prev"), f"{prefix}:{page - 1}"))
        nav.append((f"{page + 1}/{total_pages}", "mx:noop"))
        if page < total_pages - 1:
            nav.append((tr("next"), f"{prefix}:{page + 1}"))
        return nav

    def _back_cancel_row(self) -> list:
        return [(tr("back"), "mb"), (tr("cancel"), "mx")]

    def _paged_rows(self, buttons: list, meta: dict, nav_prefix: str, tail_row: list) -> list:
        rows = _rows_of_two(buttons)
        if meta["total_pages"] > 1:
            rows.append(self._nav_row(meta["page"], meta["total_pages"], nav_prefix))
        rows.append(tail_row)
        return rows

    def _provider_rows(self, providers: list, page: int) -> tuple:
        by_slug = {p.get("slug"): p for p in providers}
        buttons: list = []
        try:
            from hermes_cli.models_catalog_static import group_providers
        except Exception:
            group_providers = None
        if group_providers is not None:
            for row in group_providers([p.get("slug") for p in providers]):
                if row["kind"] == "group":
                    members = [by_slug[m] for m in row["members"] if m in by_slug]
                    count = sum(m.get("total_models", len(m.get("models", []))) for m in members)
                    label = f"{row['label']} ▸ ({count})"
                    if any(m.get("is_current") for m in members):
                        label = f"✓ {label}"
                    buttons.append((label, f"mpg:{row['group_id']}"))
                elif by_slug.get(row["slug"]) is not None:
                    buttons.append(self._provider_button(by_slug[row["slug"]]))
        else:
            buttons = [self._provider_button(p) for p in providers]
        page_buttons, meta = self._format_choice_page(buttons, page, self._PROVIDER_PAGE_SIZE)
        return self._paged_rows(page_buttons, meta, "mpv", [(tr("cancel"), "mx")]), meta["page_info"]

    def _provider_list_text(self, state: dict, page_info: str) -> str:
        model = f"`{state['current_model'] or tr('unknown_model')}`"
        return (
            f"**{tr('picker_title')}**\n\n{tr('current_model', model=model)}\n"
            f"{tr('provider_label', provider=self._provider_label(state['current_provider']))}\n\n"
            f"{tr('select_provider')}{page_info}"
        )

    def _model_rows(self, models: list, page: int, provider: Optional[dict]) -> tuple:
        try:
            from hermes_cli.model_picker_usage import picker_row_suffix
        except Exception:
            picker_row_suffix = lambda _p, _m: ""  # noqa: E731
        page_models, meta = self._format_choice_page(models, page, self._MODEL_PAGE_SIZE)
        buttons = []
        for i, model_id in enumerate(page_models):
            short = model_id.split("/")[-1] if "/" in model_id else model_id
            suffix = picker_row_suffix(provider, model_id) or ""
            room = 38 - (len(suffix) + 3 if suffix else 0)
            if len(short) > room:
                short = short[: max(room - 3, 8)] + "..."
            if suffix:
                short = f"{short} · {suffix}"
            buttons.append((short, f"mm:{meta['start'] + i}"))
        return self._paged_rows(buttons, meta, "mg", self._back_cancel_row()), meta["page_info"]

    async def send_model_picker(
        self, chat_id: str, providers: list, current_model: str, current_provider: str, session_key: str,
        on_model_selected, metadata: Optional[dict] = None,
    ) -> SendResult:
        state = {
            "msg_id": None, "providers": providers, "session_key": session_key,
            "on_model_selected": on_model_selected, "current_model": current_model,
            "current_provider": current_provider, "provider_page": 0,
        }
        rows, info = self._provider_rows(providers, 0)
        card = self._card_from_specs(rows, timeout=PICKER_DISABLE_SECS)
        result = await self._post_card(chat_id, self._provider_list_text(state, info), card, metadata)
        if result.success:
            state["msg_id"] = result.message_id
            self._model_picker_state[str(chat_id)] = state
        return result

    async def _picker_show_providers(self, chat_id: str, state: dict, page: int) -> None:
        state["provider_page"] = page
        rows, info = self._provider_rows(state["providers"], page)
        await self._replace_card(
            chat_id, state, self._provider_list_text(state, info),
            self._card_from_specs(rows, timeout=PICKER_DISABLE_SECS),
        )

    async def _picker_show_models(self, chat_id: str, state: dict, page: int) -> None:
        models = state.get("model_list", [])
        state["model_page"] = page
        provider = next((p for p in state["providers"] if p["slug"] == state.get("selected_provider")), None)
        rows, info = self._model_rows(models, page, provider)
        total = provider.get("total_models", len(models)) if provider else len(models)
        extra = f"\n__{tr('more_available', count=str(total - len(models)))}__" if total > len(models) else ""
        plan = f"\n⚠️ {provider['plan_line']}" if provider and provider.get("plan_line") else ""
        provider_name = f"**{state.get('selected_provider_name', '')}**"
        text = (
            f"**{tr('picker_title')}**\n\n"
            f"{tr('provider_label', provider=provider_name)}"
            f"{info}{plan}\n{tr('select_model')}{extra}"
        )
        await self._replace_card(chat_id, state, text, self._card_from_specs(rows, timeout=PICKER_DISABLE_SECS))

    async def _picker_switch(self, chat_id: str, state: dict, model_id: str, provider_slug: str, callback) -> None:
        self._model_picker_state.pop(chat_id, None)  # claim before the (slow) switch
        try:
            result_text = await callback(chat_id, model_id, provider_slug)
        except Exception as exc:
            logger.error("[%s] model picker switch failed: %s", self.name, exc)
            result_text = tr("switch_error", error=str(exc))
        await self._finish_picker(chat_id, state, str(result_text or ""))

    def _picker_selection(self, state: dict, raw: str) -> Optional[tuple]:
        try:
            idx = int(raw)
        except ValueError:
            return None
        models = state.get("model_list", [])
        callback = state.get("on_model_selected")
        if idx < 0 or idx >= len(models) or not callback:
            return None
        return idx, models[idx], state.get("selected_provider", ""), callback

    async def _on_model_picker_tap(self, tap: Tap, cb: str) -> None:
        chat_id = tap.chat_id
        state = self._model_picker_state.get(chat_id)
        if not state:
            await self._notify_tap(tap, tr("picker_expired"))
            return
        if cb.startswith("mp:"):
            slug = cb[3:]
            provider = next((p for p in state["providers"] if p["slug"] == slug), None)
            if not provider:
                await self._notify_tap(tap, tr("provider_not_found"))
                return
            state.update(selected_provider=slug, selected_provider_name=provider.get("name", slug),
                         model_list=provider.get("models", []))
            await self._picker_show_models(chat_id, state, 0)
        elif cb.startswith("mg:"):
            if cb[3:].isdigit():
                await self._picker_show_models(chat_id, state, int(cb[3:]))
        elif cb.startswith("mpv:"):
            if cb[4:].isdigit():
                await self._picker_show_providers(chat_id, state, int(cb[4:]))
        elif cb.startswith("mc:"):
            sel = self._picker_selection(state, cb[3:])
            if sel is None:
                await self._notify_tap(tap, tr("invalid_selection"))
                return
            _idx, model_id, provider_slug, callback = sel
            await self._picker_switch(chat_id, state, model_id, provider_slug, callback)
        elif cb.startswith("mm:"):
            sel = self._picker_selection(state, cb[3:])
            if sel is None:
                await self._notify_tap(tap, tr("invalid_selection"))
                return
            idx, model_id, provider_slug, callback = sel
            try:
                from hermes_cli.model_selection_guards import combined_selection_warning

                warning = await asyncio.to_thread(combined_selection_warning, model_id, provider=provider_slug)
            except Exception:
                warning = None
            if warning is not None:
                rows = [[(tr("switch_anyway"), f"mc:{idx}")], self._back_cancel_row()]
                await self._replace_card(
                    chat_id, state, f"⚠ **{warning.title}**\n\n{warning.message}",
                    self._card_from_specs(rows, timeout=PICKER_DISABLE_SECS),
                )
                return
            await self._picker_switch(chat_id, state, model_id, provider_slug, callback)
        elif cb.startswith("mpg:"):
            group_id = cb[4:]
            try:
                from hermes_cli.models_catalog_static import PROVIDER_GROUPS

                label, _desc, member_slugs = PROVIDER_GROUPS.get(group_id, ("", "", []))
            except Exception:
                label, member_slugs = "", []
            by_slug = {p["slug"]: p for p in state["providers"]}
            members = [by_slug[m] for m in member_slugs if m in by_slug]
            if not members:
                await self._notify_tap(tap, tr("provider_not_found"))
                return
            rows = _rows_of_two([self._provider_button(p) for p in members]) + [self._back_cancel_row()]
            text = (
                f"**{tr('picker_title')}**\n\n{tr('provider_family', family=f'**{label or group_id}**')}\n\n"
                f"{tr('select_provider')}"
            )
            await self._replace_card(chat_id, state, text, self._card_from_specs(rows, timeout=PICKER_DISABLE_SECS))
        elif cb == "mb":
            await self._picker_show_providers(chat_id, state, int(state.get("provider_page", 0) or 0))
        elif cb == "mx":
            self._model_picker_state.pop(chat_id, None)
            await self._finish_picker(chat_id, state, tr("cancelled"))
        # "mx:noop" (page counter) falls through: nothing to do

    # ------------------------------------------------------------------ tap dispatcher
    @staticmethod
    def _tap_metadata(tap: Tap) -> Optional[dict]:
        return {"thread_id": tap.thread_id} if tap.thread_id else None

    async def _handle_bot_request(self, update: dict) -> None:
        """Entry point for ``bot_request`` updates (a ``server_action`` button was pressed)."""
        request = update.get("bot_request") or {}
        for err in request.get("errors") or []:
            logger.warning(
                "[%s] button directive error: type=%s name=%s message=%s", self.name,
                err.get("type"), err.get("name"), err.get("message"),
            )
        action = request.get("server_action") or {}
        if action.get("name") != CALLBACK_ACTION:
            return
        payload = action.get("payload")
        cb = payload.get("cb") if isinstance(payload, dict) else payload
        if not isinstance(cb, str) or not cb:
            return
        chat = update.get("chat") or {}
        sender = update.get("from") or {}
        tap = Tap(
            chat_id=str(chat.get("id", "")), chat_type=str(chat.get("type") or "private"),
            login=str(sender.get("login") or ""), name=str(sender.get("display_name") or sender.get("login") or ""),
            thread_id=str(update["thread_id"]) if update.get("thread_id") else None,
        )
        if not tap.chat_id:
            return
        try:
            if cb.startswith(("mp:", "mpg:", "mpv:", "mm:", "mc:", "mb", "mx", "mg:")):
                if await self._tap_authorized(tap):
                    await self._on_model_picker_tap(tap, cb)
            elif cb.startswith("cp:"):
                if await self._tap_authorized(tap):
                    await self._on_choice_picker_tap(tap, cb)
            elif cb.startswith("ea:"):
                await self._on_approval_tap(tap, cb)
            elif cb.startswith("sc:"):
                await self._on_slash_confirm_tap(tap, cb)
            elif cb.startswith("cl:"):
                await self._on_clarify_tap(tap, cb)
            elif cb.startswith("update_prompt:"):
                await self._on_update_prompt_tap(tap, cb)
            else:
                logger.debug("[%s] unknown button callback %r", self.name, cb)
        except Exception:
            logger.exception("[%s] button callback %r failed", self.name, cb)
