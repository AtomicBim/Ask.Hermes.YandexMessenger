"""Interactive prompts for the Yandex Messenger adapter (``InteractiveMixin``).

A port of the Telegram adapter's button flows onto ``suggest_buttons``:

* every button carries ``server_action{name="hermes", payload={"cb": "<callback string>"}}``
  plus ``set_elements_state`` that disables the whole card while the bot works;
* callback strings are 1:1 with Telegram (``ea:`` ``cl:`` ``sc:`` ``update_prompt:`` ``cp:``) so the
  dispatcher logic is the same; the model picker is deliberately NOT ported (the model is set via .env);
* the Bot API has no ``answerCallbackQuery``: feedback is the card edit; refusals and expiries go
  to the tapping user as a private notice and never touch the shared card.
"""

from __future__ import annotations

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
# callback prefixes of the removed model picker (cards that may still be open in chats)
_LEGACY_MODEL_PICKER_PREFIXES = ("mp:", "mpg:", "mpv:", "mm:", "mc:", "mb", "mx", "mg:")

# --------------------------------------------------------------------------- i18n
_STRINGS = {
    "en": {
        "unauthorized": "⛔ You are not authorized to use this button.",
        "admin_only": "⛔ Only an administrator can change settings.",
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
        "unknown_model": "unknown",
        "model_locked": (
            "The model is set only by the deployment (HERMES_MODEL / HERMES_PROVIDER in .env, applied on "
            "container restart) and cannot be changed from the chat. Current model: {model}"
        ),
        "picker_expired": "This picker has expired — run the command again.",
        "invalid_selection": "Invalid selection.",
        "apply_error": "Could not apply selection: {error}",
        "no_choices": "No choices",
        "user_fallback": "user",
    },
    "ru": {
        "unauthorized": "⛔ У вас нет прав на это действие.",
        "admin_only": "⛔ Менять настройки может только администратор.",
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
        "unknown_model": "неизвестно",
        "model_locked": (
            "Модель задаётся только при развёртывании (HERMES_MODEL / HERMES_PROVIDER в .env, применяется "
            "при перезапуске контейнера) и не меняется из чата. Текущая модель: {model}"
        ),
        "picker_expired": "Выбор устарел — повторите команду.",
        "invalid_selection": "Некорректный выбор.",
        "apply_error": "Не удалось применить выбор: {error}",
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


def configured_model() -> str:
    """Best-effort current ``model.default`` (``HERMES_MODEL`` wins, as in the container init script)."""
    env = os.environ.get("HERMES_MODEL", "").strip()
    if env:
        return env
    try:
        from hermes_cli.config import load_config

        model = (load_config() or {}).get("model")
        if isinstance(model, dict):
            model = model.get("default")
        return str(model or "") or tr("unknown_model")
    except Exception:
        return tr("unknown_model")


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

    # ------------------------------------------------------------------ state
    def _init_interactive_state(self) -> None:
        self._approval_counter = itertools.count(1)
        self._approval_state: dict = {}          # approval_id -> session_key
        self._approval_request_ids: dict = {}    # approval_id -> tools.approval request_id
        self._slash_confirm_state: dict = {}     # confirm_id -> session_key
        self._clarify_state: dict = {}           # clarify_id -> session_key
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

    def _is_tap_admin(self, tap: Tap) -> bool:
        """Settings pickers (``/reasoning`` ...) are admin-only whenever slash gating is on.

        Same policy Hermes applies to the commands that open them (``gateway/slash_access.py``), so a card
        that is visible to other chat members can not be driven by a non-admin. Gating off -> everyone.
        """
        from gateway.slash_access import policy_from_extra

        scope = "dm" if tap.is_private else "group"
        policy = policy_from_extra(getattr(self.config, "extra", None) or {}, scope)
        return policy.is_admin(tap.login)

    async def _tap_admin_authorized(self, tap: Tap) -> bool:
        if not await self._tap_authorized(tap):
            return False
        if self._is_tap_admin(tap):
            return True
        await self._notify_tap(tap, tr("admin_only"))
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
            if cb.startswith(_LEGACY_MODEL_PICKER_PREFIXES):
                # a card of the removed model picker is still in some chat: refuse, never switch
                await self._notify_tap(tap, tr("model_locked", model=configured_model()))
            elif cb.startswith("cp:"):
                if await self._tap_admin_authorized(tap):
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
