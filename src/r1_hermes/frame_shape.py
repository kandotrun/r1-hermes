from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .payloads import MEDIA_CONTENT_TYPES, MEDIA_PAYLOAD_KEYS, TEXT_CONTENT_TYPES

SAFE_ENUM_KEYS = {
    "contentType",
    "content_type",
    "encoding",
    "event",
    "format",
    "mediaType",
    "method",
    "mimeType",
    "mime_type",
    "protocol",
    "role",
    "state",
    "status",
    "type",
}
SAFE_METHOD_NAMES = {
    "chat.history",
    "chat.send",
    "connect",
    "gateway.connect",
    "gateway.health",
    "health",
    "system-presence",
    "tools.catalog",
    "tools.effective",
}
SAFE_METHOD_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,12}$")
SAFE_ENUM_VALUES = frozenset(
    {
        "ack",
        "assistant",
        "base64",
        "chat",
        "completed",
        "connect.challenge",
        "connect.ok",
        "error",
        "event",
        "failed",
        "final",
        "hello-ok",
        "main",
        "node.pair.approved",
        "none",
        "ok",
        "operator",
        "pending",
        "req",
        "res",
        "running",
        "started",
        "system",
        "text",
        "unsupported",
        "user",
        "ws",
        "wss",
    }
    | MEDIA_CONTENT_TYPES
    | TEXT_CONTENT_TYPES
)
SAFE_KEY_MAX_CHARS = 32
SAFE_MIME_RE = re.compile(
    r"^(?:application|audio|image|message|model|multipart|text|video)/[A-Za-z0-9.+_-]{1,64}$"
)
SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$")
MAX_CHILDREN = 20
MAX_MEDIA_PATHS = 50
MEDIA_KEY_MARKERS = (
    "attachment",
    "audio",
    "b64",
    "base64",
    "blob",
    "bytes",
    "file",
    "image",
    "media",
    "mime",
    "video",
)


def build_frame_shape_log_fields(
    frame: Mapping[str, Any],
    *,
    authenticated: bool,
    device_id_hash: str = "",
) -> dict[str, Any]:
    """Return secret-safe diagnostic fields for a Rabbit/OpenClaw frame.

    The returned structure preserves field names, object/list shape, list lengths, string lengths,
    and selected enum-like protocol values. It must not include bearer values, device IDs, prompts,
    binary/media bytes, or arbitrary scalar values from the frame.
    """

    media_paths: list[str] = []
    method = _safe_method(frame.get("method"))
    fields: dict[str, Any] = {
        "phase": "authenticated" if authenticated else "unauthenticated",
        "frame_type": _safe_enum_value(frame.get("type")),
        "method": method,
        "media_present": _has_media_shape(frame, path="$", media_paths=media_paths),
        "media_field_paths": media_paths[:MAX_MEDIA_PATHS],
        "shape": _shape(frame, path="$"),
    }
    if device_id_hash:
        fields["device_id_hash"] = device_id_hash
    return fields


def _shape(value: Any, *, path: str, parent_key: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        raw_items = list(value.items())
        raw_keys = [str(key) for key, _child in raw_items]
        safe_keys = [_safe_key(key) for key in raw_keys]
        result: dict[str, Any] = {
            "kind": "object",
            "keys": sorted(safe_keys),
        }
        fields: dict[str, Any] = {}
        for (raw_key, child), safe_key in zip(
            raw_items[:MAX_CHILDREN],
            safe_keys[:MAX_CHILDREN],
            strict=True,
        ):
            raw_key_text = str(raw_key)
            fields[safe_key] = _shape(
                child,
                path=f"{path}.{safe_key}",
                parent_key=raw_key_text,
            )
        if len(raw_keys) > MAX_CHILDREN:
            result["truncated_keys"] = len(raw_keys) - MAX_CHILDREN
        result["fields"] = fields
        return result
    if isinstance(value, list | tuple | set | frozenset):
        items_value = list(value) if isinstance(value, set | frozenset) else value
        items = [
            _shape(item, path=f"{path}[{index}]", parent_key=parent_key)
            for index, item in enumerate(items_value[:MAX_CHILDREN])
        ]
        result = {"kind": "list", "len": len(items_value), "items": items}
        if len(items_value) > MAX_CHILDREN:
            result["truncated_items"] = len(items_value) - MAX_CHILDREN
        return result
    if isinstance(value, str):
        result = {"kind": "string", "chars": len(value)}
        if parent_key in SAFE_ENUM_KEYS:
            safe_value = _safe_enum_value(value)
            if safe_value:
                result["value"] = safe_value
        return result
    if isinstance(value, bool):
        return {"kind": "boolean"}
    if isinstance(value, int | float):
        return {"kind": "number"}
    if value is None:
        return {"kind": "null"}
    if isinstance(value, bytes | bytearray):
        return {"kind": "bytes", "len": len(value)}
    return {"kind": value.__class__.__name__}


def _has_media_shape(value: Any, *, path: str, media_paths: list[str]) -> bool:
    if isinstance(value, Mapping):
        present = False
        content_type = value.get("type")
        if isinstance(content_type, str) and content_type.strip().lower() in MEDIA_CONTENT_TYPES:
            present = True
            _append_media_path(media_paths, path)
        for key, child in value.items():
            child_path = f"{path}.{_safe_key(str(key))}"
            if _is_media_field_key(str(key)):
                present = True
                _append_media_path(media_paths, child_path)
            if _has_media_shape(child, path=child_path, media_paths=media_paths):
                present = True
        return present
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        present = False
        for index, child in enumerate(value):
            if _has_media_shape(child, path=f"{path}[{index}]", media_paths=media_paths):
                present = True
        return present
    if isinstance(value, bytes | bytearray):
        _append_media_path(media_paths, path)
        return True
    return False


def _is_media_field_key(key: str) -> bool:
    normalized = key.strip().replace("_", "").replace("-", "").lower()
    return key in MEDIA_PAYLOAD_KEYS or any(marker in normalized for marker in MEDIA_KEY_MARKERS)


def _append_media_path(media_paths: list[str], path: str) -> None:
    if len(media_paths) < MAX_MEDIA_PATHS and path not in media_paths:
        media_paths.append(path)


def _safe_method(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if (text in SAFE_METHOD_NAMES or SAFE_METHOD_RE.fullmatch(text)) and not _looks_secret_like(
        text
    ):
        return text
    return _unsafe_marker(text)


def _safe_enum_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text in SAFE_ENUM_VALUES and not _looks_secret_like(text):
        return text
    if SAFE_MIME_RE.fullmatch(text) and not _looks_secret_like(text):
        return text
    return _unsafe_marker(text)


def _safe_key(value: str) -> str:
    text = value.strip()
    if len(text) <= SAFE_KEY_MAX_CHARS and SAFE_KEY_RE.fullmatch(text) and not _looks_secret_like(
        text
    ):
        return text
    return _unsafe_marker(text, prefix="unsafe-key")


def _looks_secret_like(text: str) -> bool:
    if len(text) < 24:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_+=./:-]+", text):
        return False
    classes = sum(
        (
            any(char.islower() for char in text),
            any(char.isupper() for char in text),
            any(char.isdigit() for char in text),
            any(char in "_+=/:-" for char in text),
        )
    )
    return classes >= 3


def _unsafe_marker(text: str, *, prefix: str = "unsafe-string") -> str:
    return f"[{prefix}:chars={len(text)}]"
