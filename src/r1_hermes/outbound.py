from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_OUTBOUND_TEXT_MAX_CHARS = 8_192
DEFAULT_OUTBOUND_EVENT_MAX_BYTES = 64 * 1024
MAX_OUTBOUND_IDENTIFIER_CHARS = 120

TRUNCATED_RESPONSE_TEXT = "Hermes response truncated by R1 outbound limit."
MIN_TRUNCATED_RESPONSE_TEXT = "truncated"
_SAFE_OUTBOUND_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]+")
_UNSAFE_IDENTIFIER_MARKERS = ("secret", "token", "bearer", "auth", "password", "key")


@dataclass(frozen=True)
class BoundedText:
    text: str
    original_chars: int
    returned_chars: int
    max_chars: int
    truncated: bool


def bound_outbound_text(value: Any, *, max_chars: int) -> BoundedText:
    """Return bounded R1-visible text without leaking a prefix of oversized output."""

    limit = int(max_chars)
    if limit < 1:
        raise ValueError("max_chars must be at least 1")

    text = str(value or "")
    original_chars = len(text)
    if original_chars <= limit:
        return BoundedText(
            text=text,
            original_chars=original_chars,
            returned_chars=original_chars,
            max_chars=limit,
            truncated=False,
        )

    safe_text = _truncated_text_for_limit(limit)
    return BoundedText(
        text=safe_text,
        original_chars=original_chars,
        returned_chars=len(safe_text),
        max_chars=limit,
        truncated=True,
    )


def truncated_outbound_text(*, max_chars: int) -> str:
    limit = int(max_chars)
    if limit < 1:
        raise ValueError("max_chars must be at least 1")
    return _truncated_text_for_limit(limit)


def bound_outbound_identifier(value: Any, *, fallback_prefix: str = "id") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback_prefix
    if (
        len(text) < MAX_OUTBOUND_IDENTIFIER_CHARS
        and _SAFE_OUTBOUND_IDENTIFIER.fullmatch(text)
        and not any(marker in text.lower() for marker in _UNSAFE_IDENTIFIER_MARKERS)
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{fallback_prefix}-sha256-{digest}"


def event_size_bytes(payload: dict[str, Any]) -> int:
    """Return a conservative byte size for the JSON event aiohttp will send."""

    return len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))


def _truncated_text_for_limit(limit: int) -> str:
    if limit >= len(TRUNCATED_RESPONSE_TEXT):
        return TRUNCATED_RESPONSE_TEXT
    if limit >= len(MIN_TRUNCATED_RESPONSE_TEXT):
        return MIN_TRUNCATED_RESPONSE_TEXT
    return MIN_TRUNCATED_RESPONSE_TEXT[:limit]
