"""Builders for ``suggest_buttons`` (the non-deprecated keyboard)."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

MAX_BUTTONS = 100
MAX_DIRECTIVES = 3
MAX_FIELD_LEN = 255
MAX_DISABLE_TIMEOUT = 60


def _check_len(label: str, value: str) -> str:
    if len(value) > MAX_FIELD_LEN:
        raise ValueError(f"button {label} exceeds {MAX_FIELD_LEN} chars")
    return value


def server_action_button(
    id: str,
    title: str,
    name: str,
    payload: Any,
    disable_ids: Optional[Sequence[str]] = None,
    disable_timeout: int = MAX_DISABLE_TIMEOUT,
) -> dict:
    """Button whose tap arrives as ``bot_request.server_action`` and (optionally)
    disables ``disable_ids`` on the client for ``disable_timeout`` seconds (max 60)."""
    directives: list = [{"type": "server_action", "name": name, "payload": payload}]
    if disable_ids:
        directives.append(
            {
                "type": "set_elements_state",
                "ids": list(disable_ids),
                "state": "disabled",
                "timeout_seconds": max(1, min(int(disable_timeout), MAX_DISABLE_TIMEOUT)),
            }
        )
    return _button(id, title, directives)


def url_button(id: str, title: str, uri: str) -> dict:
    return _button(id, title, [{"type": "open_uri", "uri": uri}])


def _button(id: str, title: str, directives: list) -> dict:
    if len(directives) > MAX_DIRECTIVES:
        raise ValueError(f"a button takes at most {MAX_DIRECTIVES} directives")
    return {"id": _check_len("id", str(id)), "title": _check_len("title", str(title)), "directives": directives}


def keyboard(rows: Iterable[Sequence[dict]], persist: bool = True) -> dict:
    """``SuggestButtons`` with one inner list per visual row.

    ``persist=True`` keeps the buttons visible after the message stops being the
    last one in the chat (essential while a reply streams in).
    """
    grid = [list(row) for row in rows if row]
    total = sum(len(row) for row in grid)
    if total > MAX_BUTTONS:
        raise ValueError(f"at most {MAX_BUTTONS} buttons allowed, got {total}")
    ids = [b.get("id") for row in grid for b in row if b.get("id")]
    if len(ids) != len(set(ids)):
        raise ValueError("button ids must be unique within a keyboard")
    return {"layout": "true", "persist": bool(persist), "buttons": grid}


def empty_keyboard() -> dict:
    """A keyboard with no buttons (used to try to clear buttons on edit)."""
    return {"layout": "false", "persist": False, "buttons": []}


def action_card(
    rows: Sequence[Sequence[tuple]],
    *,
    name: str = "hermes",
    persist: bool = True,
    disable_timeout: int = MAX_DISABLE_TIMEOUT,
) -> dict:
    """Build a keyboard where every button disables the whole card while the bot works.

    ``rows`` is ``[[(button_id, title, payload), ...], ...]``.
    """
    all_ids = [bid for row in rows for (bid, _t, _p) in row]
    built = [
        [
            server_action_button(bid, title, name, payload, disable_ids=all_ids, disable_timeout=disable_timeout)
            for (bid, title, payload) in row
        ]
        for row in rows
    ]
    return keyboard(built, persist=persist)
