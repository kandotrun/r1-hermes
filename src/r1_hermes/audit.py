from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

AUDIT_LOGGER_NAME = "r1_hermes.audit"
AUDIT_LOGGER = logging.getLogger(AUDIT_LOGGER_NAME)

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def hash_identifier(value: Any) -> str:
    """Return a stable short digest for local log correlation without raw identifiers."""

    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def audit_log(level: str, event: str, **fields: Any) -> None:
    """Emit one redacted structured audit event as JSON.

    Callers must pass only non-secret values, safe hashes, counts, codes, and fixed strings.
    This helper deliberately avoids logging exception reprs, prompts, auth headers, or tokens.
    """

    normalized_level = level.lower()
    payload: dict[str, Any] = {
        "event": event,
        "level": normalized_level,
        "ts_ms": int(time.time() * 1000),
    }
    for key, value in sorted(fields.items()):
        safe_value = _json_safe_value(value)
        if safe_value is not None:
            payload[key] = safe_value
    AUDIT_LOGGER.log(
        _LEVELS.get(normalized_level, logging.INFO),
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(child) for key, child in value.items()}
    return value.__class__.__name__
