"""Typed errors for the Yandex Messenger Bot API client.

``classify`` maps an API failure onto the platform-neutral kinds Hermes uses in
``SendResult.error_kind`` (``SEND_ERROR_KINDS``), so adapters can branch without
substring-matching the description.
"""

from __future__ import annotations

from typing import Optional

KIND_TOO_LONG = "too_long"
KIND_BAD_FORMAT = "bad_format"
KIND_FORBIDDEN = "forbidden"
KIND_NOT_FOUND = "not_found"
KIND_RATE_LIMITED = "rate_limited"
KIND_TRANSIENT = "transient"
KIND_UNKNOWN = "unknown"

ERROR_KINDS = frozenset(
    {
        KIND_TOO_LONG,
        KIND_BAD_FORMAT,
        KIND_FORBIDDEN,
        KIND_NOT_FOUND,
        KIND_RATE_LIMITED,
        KIND_TRANSIENT,
        KIND_UNKNOWN,
    }
)


def classify(status: Optional[int], description: str = "", *, transport: bool = False) -> str:
    """Return one of ``ERROR_KINDS`` for an HTTP status + API ``description``."""
    if transport:
        return KIND_TRANSIENT
    blob = (description or "").lower()
    if status == 429 or "too many requests" in blob or "rate limit" in blob:
        return KIND_RATE_LIMITED
    if status is not None and status >= 500:
        return KIND_TRANSIENT
    if "too long" in blob or "too large" in blob or "6000" in blob or "message_too_long" in blob:
        return KIND_TOO_LONG
    if status in (401, 403) or any(
        needle in blob
        for needle in ("unauthorized", "forbidden", "not a member", "no rights", "not allowed", "privacy")
    ):
        return KIND_FORBIDDEN
    if status == 404 or "not found" in blob or "no such" in blob:
        return KIND_NOT_FOUND
    if any(needle in blob for needle in ("markdown", "parse", "entities", "invalid format", "bad format")):
        return KIND_BAD_FORMAT
    return KIND_UNKNOWN


class YMApiError(Exception):
    """Base error: a Bot API call failed (HTTP error, ``ok: false`` or transport)."""

    def __init__(
        self,
        description: str = "",
        *,
        status: Optional[int] = None,
        method: str = "",
        retry_after: Optional[float] = None,
        kind: Optional[str] = None,
    ) -> None:
        self.description = description or ""
        self.status = status
        self.method = method
        self.retry_after = retry_after
        self.kind = kind or classify(status, self.description)
        super().__init__(self.__str__())

    def __str__(self) -> str:  # never includes request headers/tokens
        where = f" {self.method}" if self.method else ""
        code = f" [{self.status}]" if self.status is not None else ""
        return f"Yandex API{where}{code}: {self.description or 'error'}"


class YMRateLimited(YMApiError):
    """HTTP 429. ``retry_after`` (seconds) is set when the server supplied it."""


class YMForbidden(YMApiError):
    """401/403 or a permission-like description (bot not in chat, privacy)."""


class YMNotFound(YMApiError):
    """404 / unknown chat, message or file."""


class YMBadRequest(YMApiError):
    """Other 4xx: invalid payload."""


class YMTransportError(YMApiError):
    """Connection / timeout / 5xx after retries."""


class YMFileTooLarge(YMApiError):
    """A download exceeded the caller-supplied ``max_bytes`` limit."""


def error_for(
    status: Optional[int],
    description: str,
    *,
    method: str = "",
    retry_after: Optional[float] = None,
) -> YMApiError:
    """Build the most specific ``YMApiError`` subclass for a failed response."""
    kind = classify(status, description)
    cls = {
        KIND_RATE_LIMITED: YMRateLimited,
        KIND_FORBIDDEN: YMForbidden,
        KIND_NOT_FOUND: YMNotFound,
        KIND_TRANSIENT: YMTransportError,
    }.get(kind)
    if cls is None:
        cls = YMBadRequest if status is not None and 400 <= status < 500 else YMApiError
    return cls(description, status=status, method=method, retry_after=retry_after, kind=kind)
