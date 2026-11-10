"""Markdown conversion and message splitting for Yandex Messenger.

Yandex syntax: ``**bold**``, ``__italic__`` (double underscore!), ``~~strike~~``,
``++underline++``, `` `code` ``, fenced blocks and ``[text](url)``. No headings, no tables.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

MAX_MESSAGE_LENGTH = 6000

_FENCE_RE = re.compile(r"```[^\n]*\n.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD_STAR_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_BOLD_UNDER_RE = re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w_])_(?![\s_])(.+?)(?<![\s_])_(?![\w_])")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")

# Private-use sentinels: cannot occur in model output, survive every regex above.
_B_OPEN, _B_CLOSE = "\ue000", "\ue001"
_STASH = "\ue002{}\ue003"


def _split_cells(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _convert_tables(text: str) -> str:
    """Pipe tables -> bullet lists (Yandex renders no tables)."""
    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        if (
            "|" in lines[i]
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1])
            and "|" in lines[i + 1]
        ):
            headers = _split_cells(lines[i])
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _split_cells(lines[i])
                head = cells[0] if cells else ""
                rest = [
                    f"{headers[j] if j < len(headers) and headers[j] else j}: {cells[j]}"
                    for j in range(1, len(cells))
                    if cells[j]
                ]
                body = f"**{head}**" if head else ""
                if rest:
                    body = f"{body} — " + "; ".join(rest) if body else "; ".join(rest)
                out.append(f"- {body}".rstrip())
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def to_yandex_markdown(text: str) -> str:
    """Convert model Markdown to Yandex Messenger markup (code is never modified)."""
    if not text:
        return text

    stash: List[str] = []

    def hide(match: "re.Match[str]") -> str:
        stash.append(match.group(0))
        return _STASH.format(len(stash) - 1)

    work = _FENCE_RE.sub(hide, text)
    work = _INLINE_CODE_RE.sub(hide, work)

    work = _convert_tables(work)
    work = _IMAGE_RE.sub(lambda m: f"[{m.group(1) or m.group(2)}]({m.group(2)})", work)
    work = _HEADING_RE.sub(lambda m: f"**{m.group(1)}**", work)

    # Bold first (standard ``**x**`` and ``__x__``) behind sentinels, then italics, then restore.
    work = _BOLD_STAR_RE.sub(lambda m: f"{_B_OPEN}{m.group(1)}{_B_CLOSE}", work)
    work = _BOLD_UNDER_RE.sub(lambda m: f"{_B_OPEN}{m.group(1)}{_B_CLOSE}", work)
    work = _ITALIC_STAR_RE.sub(lambda m: f"__{m.group(1)}__", work)
    work = _ITALIC_UNDER_RE.sub(lambda m: f"__{m.group(1)}__", work)
    work = work.replace(_B_OPEN, "**").replace(_B_CLOSE, "**")

    def restore(match: "re.Match[str]") -> str:
        return stash[int(match.group(1))]

    return re.sub("\ue002(\\d+)\ue003", restore, work)


def _fence_state(body: str, in_code: bool, lang: str) -> Tuple[bool, str]:
    """Walk ``body`` line by line and report whether a fence is still open at its end."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                in_code, lang = False, ""
            else:
                in_code, lang = True, stripped[3:].strip()
    return in_code, lang


def _find_cut(text: str, budget: int) -> int:
    """Best cut position <= ``budget``: paragraph, then line, then word, else hard cut."""
    region = text[:budget]
    for sep, floor in (("\n\n", 0.5), ("\n", 0.4), (" ", 0.4)):
        pos = region.rfind(sep)
        if pos > 0 and pos >= int(budget * floor):
            return pos
    return budget


def split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """Split ``text`` into chunks of at most ``limit`` chars.

    Cuts prefer paragraph, then line, then word boundaries. A chunk cut inside a fenced
    block is closed with a fence and the next chunk reopens it (same language tag).
    """
    if not text:
        return []
    if limit < 32:
        raise ValueError("limit too small")
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    carry: Optional[str] = None  # language tag when the previous chunk ended inside a fence
    close = "\n```"
    while remaining:
        prefix = f"```{carry}\n" if carry is not None else ""
        if len(prefix) + len(remaining) <= limit:
            chunks.append(prefix + remaining)
            break
        budget = max(1, limit - len(prefix))
        cut = _find_cut(remaining, budget)
        in_code, lang = _fence_state(remaining[:cut], carry is not None, carry or "")
        if in_code and len(prefix) + cut + len(close) > limit:
            # the closing fence must fit too: re-cut with room for it
            cut = _find_cut(remaining, max(1, limit - len(prefix) - len(close)))
            in_code, lang = _fence_state(remaining[:cut], carry is not None, carry or "")
        body = remaining[:cut]
        remaining = remaining[cut:]
        remaining = remaining.lstrip("\n") if in_code else remaining.lstrip()
        chunks.append(prefix + body + (close if in_code else ""))
        carry = lang if in_code else None
    return chunks
