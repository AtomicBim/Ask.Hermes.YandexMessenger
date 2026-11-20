"""One-time-per-day welcome that offers to connect Yandex Mail / Calendar / Disk.

No LLM is involved. A service counts as connected when the user's agent has stored its access in the
secrets directory (``<HERMES_HOME>/.secrets``, the place the shared skills tell the agent to use):
a non-empty file whose NAME mentions the service. File CONTENTS are never read: only names and sizes.

The welcome is sent on the first private message of a day while at least one service is missing
(nothing connected or only some of them); once all three are connected it is never sent again.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SECRETS_DIRNAME = ".secrets"

# service id -> (display name, request example, keywords of an access-file name)
SERVICES = (
    ("mail", "Почта", "подключи почту", ("mail", "imap", "почт")),
    ("calendar", "Календарь", "подключи календарь", ("calendar", "caldav", "календар")),
    ("disk", "Диск", "подключи диск", ("disk", "webdav", "yd", "диск")),
)

_MAX_FILES = 200  # a corrupt/huge directory must never slow the inbound path down


@dataclass(frozen=True)
class Status:
    connected: tuple   # service ids with a stored, non-empty access file
    missing: tuple     # service ids without one

    @property
    def complete(self) -> bool:
        return not self.missing


def secrets_dir(home: Optional[Path]) -> Optional[Path]:
    return None if home is None else home / SECRETS_DIRNAME


def _name_tokens(name: str) -> set:
    return {t for t in re.split(r"[^0-9a-zа-яё]+", name.lower()) if t}


def _matches(name: str, keywords: tuple) -> bool:
    lowered = name.lower()
    tokens = _name_tokens(name)
    # short keywords ("yd") must be a whole token, otherwise "yd" would match "dyd.txt"
    return any((kw in tokens) if len(kw) <= 2 else (kw in lowered) for kw in keywords)


def connection_status(home: Optional[Path]) -> Status:
    """Which services have a stored access file. Unreadable/missing directory -> nothing connected."""
    found: set = set()
    directory = secrets_dir(home)
    try:
        entries = sorted(directory.iterdir())[:_MAX_FILES] if directory and directory.is_dir() else []
    except OSError:
        logger.debug("onboarding: cannot list %s", directory, exc_info=True)
        entries = []
    for entry in entries:
        try:
            if not entry.is_file() or entry.stat().st_size == 0:
                continue
        except OSError:
            continue
        for sid, _title, _example, keywords in SERVICES:
            if _matches(entry.name, keywords):
                found.add(sid)
    connected = tuple(sid for sid, *_ in SERVICES if sid in found)
    missing = tuple(sid for sid, *_ in SERVICES if sid not in found)
    return Status(connected, missing)


def welcome_text(status: Status) -> str:
    """Russian welcome listing only what is not connected yet."""
    titles = {sid: (title, example) for sid, title, example, _kw in SERVICES}
    lines = []
    if status.connected:
        done = ", ".join(titles[s][0] for s in status.connected)
        lines.append(f"Привет! Сейчас подключено: {done}. Могу подключить и остальное, чтобы работать с вашей корпоративной Яндекс-почтой, календарём и диском.")
    else:
        lines.append("Привет! Я могу работать с вашей корпоративной Яндекс Почтой, Календарём и Диском.")
    lines.append("")
    lines.append("Не подключено:")
    for sid in status.missing:
        title, example = titles[sid]
        lines.append(f"• {title}: напишите «{example}»")
    lines.append("")
    lines.append(
        "Понадобится пароль приложения Яндекса для нужного сервиса (создаётся в настройках аккаунта, "
        "в разделе «Пароли приложений»), я подскажу пошагово. Это необязательно: без подключения я работаю как обычно."
    )
    return "\n".join(lines)


def marker_path(home: Optional[Path], login: str) -> Optional[Path]:
    """Per-login marker; its mtime date is the day of the last welcome."""
    safe = re.sub(r"[^A-Za-z0-9._@-]", "_", (login or "").strip().lower()).lstrip(".")
    if home is None or not safe:
        return None
    return home / "yandex_messenger" / "onboarded" / safe


def _today(now: Optional[_dt.datetime] = None) -> _dt.date:
    return (now or _dt.datetime.now()).date()


def due(marker: Path, now: Optional[_dt.datetime] = None) -> bool:
    """True when no welcome was sent today (missing marker = never sent)."""
    try:
        mtime = marker.stat().st_mtime
    except FileNotFoundError:
        return True
    except OSError:
        return False  # unknown state: better skip than spam
    return _dt.datetime.fromtimestamp(mtime).date() < _today(now)


def claim(marker: Path) -> None:
    """Record today's welcome BEFORE it is sent, so a failed send never causes a repeat."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch(exist_ok=True)
    marker.write_text(_today().isoformat(), encoding="utf-8")  # also refreshes mtime on an existing file
