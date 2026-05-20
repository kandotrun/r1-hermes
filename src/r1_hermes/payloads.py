from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class PayloadParseError(ValueError):
    """Safe parser error for untrusted Rabbit R1/OpenClaw request payloads."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


UNSUPPORTED_MEDIA_CODE = "UNSUPPORTED_MEDIA"
UNSUPPORTED_MEDIA_MESSAGE = "unsupported media content"
TEXT_CONTENT_TYPES = {"text", "input_text"}
MEDIA_CONTENT_TYPES = {
    "attachment",
    "audio",
    "file",
    "image",
    "input_audio",
    "input_file",
    "input_image",
    "input_video",
    "media",
    "video",
}
MEDIA_PAYLOAD_KEYS = {
    "audio",
    "audio_url",
    "b64_json",
    "base64",
    "blob",
    "bytes",
    "data",
    "file",
    "image",
    "image_url",
    "media",
    "mimeType",
    "mime_type",
    "uri",
    "url",
}


@dataclass(frozen=True)
class ConnectRequest:
    auth_token: str = field(repr=False)
    device_id: str
    display_name: str = "Rabbit R1"


@dataclass(frozen=True)
class ChatSendRequest:
    message: str = field(repr=False)
    session_key: str = "main"
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ChatHistoryRequest:
    session_key: str = "main"


def request_params(frame: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized request params from either `params` or `payload` object shapes."""
    if not isinstance(frame, Mapping):
        raise PayloadParseError("BAD_REQUEST", "request frame must be an object")

    normalized: dict[str, Any] = {}
    saw_params = False
    for key in ("payload", "params"):
        if key not in frame:
            continue
        saw_params = True
        value = frame.get(key)
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise PayloadParseError("BAD_REQUEST", f"{key} must be an object")
        normalized.update(dict(value))
    if not saw_params:
        return {}
    return normalized


def parse_connect_params(params: Mapping[str, Any]) -> ConnectRequest:
    data = _require_mapping(params, "params")
    auth = _optional_mapping(data.get("auth"), "auth")
    device = _optional_device_mapping(data.get("device"))
    client = _optional_mapping(data.get("client"), "client")

    auth_token = _first_text(
        (auth, ("token", "authToken", "gatewayToken", "deviceToken", "bearerToken")),
        (data, ("authToken", "token", "gatewayToken", "deviceToken")),
    )
    if auth_token is None:
        raise PayloadParseError("BAD_REQUEST", "auth token is required")

    device_id = _first_text(
        (device, ("id", "deviceId", "device_id", "serial", "serialNumber")),
        (data, ("deviceId", "device_id")),
    )
    if device_id is None:
        raise PayloadParseError("BAD_REQUEST", "device.id is required")

    display_name = _first_text(
        (client, ("displayName", "display_name", "name")),
        (device, ("displayName", "display_name", "name", "model")),
        (data, ("displayName", "display_name", "clientName", "client_name")),
    )
    return ConnectRequest(
        auth_token=auth_token,
        device_id=device_id,
        display_name=(display_name or "Rabbit R1")[:120],
    )


def parse_chat_send_params(params: Mapping[str, Any]) -> ChatSendRequest:
    data = _require_mapping(params, "params")
    message = _extract_message(data)
    if message is None or not message.strip():
        raise PayloadParseError("EMPTY_MESSAGE", "message is required")

    session_key = _extract_session_key(data)
    idempotency_key = _first_text(
        (data, ("idempotencyKey", "idempotency_key", "requestId", "request_id", "runId")),
    )
    return ChatSendRequest(
        message=message,
        session_key=(session_key or "main")[:120],
        idempotency_key=idempotency_key,
    )


def parse_chat_history_params(params: Mapping[str, Any]) -> ChatHistoryRequest:
    data = _require_mapping(params, "params")
    session_key = _extract_session_key(data)
    return ChatHistoryRequest(session_key=session_key or "main")


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PayloadParseError("BAD_REQUEST", f"{label} must be an object")
    return value


def _optional_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PayloadParseError("BAD_REQUEST", f"{label} must be an object")
    return value


def _optional_device_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        return {"id": value}
    return _optional_mapping(value, "device")


def _first_text(*sources: tuple[Mapping[str, Any], Sequence[str]]) -> str | None:
    for source, keys in sources:
        for key in keys:
            value = source.get(key)
            text = _clean_text(value)
            if text is not None:
                return text
    return None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _extract_message(data: Mapping[str, Any]) -> str | None:
    message = data.get("message")
    nested_content_text = None
    if isinstance(message, Mapping) and "content" in message:
        nested_content_text = _content_to_text(message.get("content"))

    top_level_content_text = None
    if "content" in data:
        top_level_content_text = _content_to_text(data.get("content"))

    direct = _first_text((data, ("message", "text", "prompt", "input")))
    if direct is not None:
        return direct

    if isinstance(message, Mapping):
        text = _first_text((message, ("text", "body", "prompt", "input")))
        if text is not None:
            return text
        return nested_content_text

    return top_level_content_text


def _extract_session_key(data: Mapping[str, Any]) -> str | None:
    session = _optional_mapping(data.get("session"), "session")
    conversation = _optional_mapping(data.get("conversation"), "conversation")
    session_key = _first_text(
        (data, ("sessionKey", "session_key", "sessionId", "session_id", "conversationId")),
        (session, ("key", "id", "sessionKey", "sessionId")),
        (conversation, ("id", "key")),
    )
    return session_key[:120] if session_key is not None else None


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, (bytes, bytearray)):
        _raise_unsupported_media()
    if isinstance(content, Mapping):
        content_type = _content_type(content)
        if _is_text_content_part(content, content_type):
            text = content.get("text")
            return text.strip() if isinstance(text, str) and text.strip() else None
        if _is_unsupported_content_part(content, content_type):
            _raise_unsupported_media()
        return None
    if not isinstance(content, Sequence):
        return None

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, (bytes, bytearray)):
            _raise_unsupported_media()
        if isinstance(item, Mapping):
            content_type = _content_type(item)
            if _is_text_content_part(item, content_type):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            if _is_unsupported_content_part(item, content_type):
                _raise_unsupported_media()

    text = "".join(parts).strip()
    return text or None


def _content_type(item: Mapping[str, Any]) -> str | None:
    value = item.get("type")
    return value.strip().lower() if isinstance(value, str) else None


def _is_text_content_part(item: Mapping[str, Any], content_type: str | None = None) -> bool:
    content_type = content_type if content_type is not None else _content_type(item)
    return content_type in TEXT_CONTENT_TYPES and not _has_media_payload(item)


def _is_unsupported_content_part(
    item: Mapping[str, Any], content_type: str | None = None
) -> bool:
    if _has_media_payload(item):
        return True
    content_type = content_type if content_type is not None else _content_type(item)
    if content_type is not None:
        if content_type in TEXT_CONTENT_TYPES:
            return False
        if content_type in MEDIA_CONTENT_TYPES:
            return True
        return True
    return True


def _has_media_payload(item: Mapping[str, Any]) -> bool:
    return any(key in item for key in MEDIA_PAYLOAD_KEYS)


def _raise_unsupported_media() -> None:
    raise PayloadParseError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
