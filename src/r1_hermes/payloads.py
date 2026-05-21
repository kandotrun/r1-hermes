from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse


class PayloadParseError(ValueError):
    """Safe parser error for untrusted Rabbit R1/OpenClaw request payloads."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


UNSUPPORTED_MEDIA_CODE = "UNSUPPORTED_MEDIA"
UNSUPPORTED_MEDIA_MESSAGE = "unsupported media content"
TEXT_CONTENT_TYPES = {"text", "input_text"}
IMAGE_CONTENT_TYPES = {"image", "input_image"}
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
IMAGE_CONTAINER_KEYS = {"attachments", "media"}
IMAGE_VALUE_KEYS = {"image", "image_url", "input_image"}
IMAGE_DATA_KEYS = ("data", "b64_json", "base64", "bytes", "blob")
IMAGE_URL_KEYS = ("image_url", "url", "uri")
IMAGE_NESTED_KEYS = ("image", "input_image", "media", "file")
IMAGE_MIME_KEYS = (
    "mediaType",
    "media_type",
    "mimeType",
    "mime_type",
    "contentType",
    "content_type",
)
IMAGE_FILENAME_KEYS = ("filename", "fileName", "name")
SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
IMAGE_MIME_EXTENSIONS = {
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class ConnectRequest:
    auth_token: str = field(repr=False)
    device_id: str
    display_name: str = "Rabbit R1"


@dataclass(frozen=True)
class ImageAttachment:
    mime_type: str
    source_field: str
    filename: str | None
    extension: str | None
    size_bytes: int
    content_hash: str | None = None
    source_hash: str | None = None
    data: bytes | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ChatSendRequest:
    message: str = field(repr=False)
    session_key: str = "main"
    idempotency_key: str | None = None
    attachments: tuple[ImageAttachment, ...] = ()


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
    message, attachments = _extract_message_and_attachments(data)
    if (message is None or not message.strip()) and not attachments:
        raise PayloadParseError("EMPTY_MESSAGE", "message is required")

    session_key = _extract_session_key(data)
    idempotency_key = _first_text(
        (data, ("idempotencyKey", "idempotency_key", "requestId", "request_id", "runId")),
    )
    return ChatSendRequest(
        message=(message or ""),
        session_key=(session_key or "main")[:120],
        idempotency_key=idempotency_key,
        attachments=attachments,
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


def _extract_message_and_attachments(
    data: Mapping[str, Any],
) -> tuple[str | None, tuple[ImageAttachment, ...]]:
    message = data.get("message")
    attachments: list[ImageAttachment] = []
    nested_content_text = None
    if isinstance(message, Mapping) and "content" in message:
        nested_content_text, nested_content_attachments = _content_to_text_and_attachments(
            message.get("content"),
            source_path="message.content",
        )
        attachments.extend(nested_content_attachments)

    top_level_content_text = None
    if "content" in data:
        top_level_content_text, top_level_content_attachments = _content_to_text_and_attachments(
            data.get("content"),
            source_path="content",
        )
        attachments.extend(top_level_content_attachments)

    attachments.extend(_extract_top_level_image_attachments(data))

    direct = _first_text((data, ("message", "text", "prompt", "input")))
    if direct is not None:
        return direct, tuple(attachments)

    if isinstance(message, Mapping):
        text = _first_text((message, ("text", "body", "prompt", "input")))
        if text is not None:
            return text, tuple(attachments)
        return nested_content_text, tuple(attachments)

    return top_level_content_text, tuple(attachments)


def _extract_message(data: Mapping[str, Any]) -> str | None:
    message, _attachments = _extract_message_and_attachments(data)
    return message


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
    text, _attachments = _content_to_text_and_attachments(content, source_path="content")
    return text


def _content_to_text_and_attachments(
    content: Any,
    *,
    source_path: str,
) -> tuple[str | None, tuple[ImageAttachment, ...]]:
    if isinstance(content, str):
        if _looks_like_image_data_url(content):
            return None, (_image_attachment_from_string(content, source_path=source_path),)
        return content.strip() or None, ()
    if isinstance(content, (bytes, bytearray)):
        _raise_unsupported_media()
    if isinstance(content, Mapping):
        content_type = _content_type(content)
        if _is_text_content_part(content, content_type):
            text = content.get("text")
            return text.strip() if isinstance(text, str) and text.strip() else None, ()
        if _is_image_content_part(content, content_type):
            return None, (_image_attachment_from_mapping(content, source_path=source_path),)
        _raise_unsupported_media()
    if not isinstance(content, Sequence):
        return None, ()

    parts: list[str] = []
    attachments: list[ImageAttachment] = []
    for item in content:
        if isinstance(item, str):
            if _looks_like_image_data_url(item):
                attachments.append(_image_attachment_from_string(item, source_path=source_path))
            else:
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
            if _is_image_content_part(item, content_type):
                attachments.append(_image_attachment_from_mapping(item, source_path=source_path))
                continue
            _raise_unsupported_media()

    text = "".join(parts).strip()
    return text or None, tuple(attachments)


def _content_type(item: Mapping[str, Any]) -> str | None:
    value = item.get("type")
    return value.strip().lower() if isinstance(value, str) else None


def _is_text_content_part(item: Mapping[str, Any], content_type: str | None = None) -> bool:
    content_type = content_type if content_type is not None else _content_type(item)
    return content_type in TEXT_CONTENT_TYPES and not _has_media_payload(item)


def _is_image_content_part(item: Mapping[str, Any], content_type: str | None = None) -> bool:
    content_type = content_type if content_type is not None else _content_type(item)
    if content_type in IMAGE_CONTENT_TYPES:
        return True
    if content_type in TEXT_CONTENT_TYPES:
        return False
    return any(key in item for key in IMAGE_VALUE_KEYS | set(IMAGE_DATA_KEYS) | set(IMAGE_URL_KEYS))


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


def _extract_top_level_image_attachments(data: Mapping[str, Any]) -> tuple[ImageAttachment, ...]:
    attachments: list[ImageAttachment] = []
    for key in IMAGE_CONTAINER_KEYS:
        if key in data:
            attachments.extend(_image_attachments_from_container(data.get(key), source_path=key))
    for key in IMAGE_VALUE_KEYS:
        if key in data:
            attachments.append(_image_attachment_from_value(data.get(key), source_path=key))
    return tuple(attachments)


def _image_attachments_from_container(
    value: Any,
    *,
    source_path: str,
) -> tuple[ImageAttachment, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (_image_attachment_from_mapping(value, source_path=source_path),)
    if isinstance(value, str):
        return (_image_attachment_from_string(value, source_path=source_path),)
    if isinstance(value, (bytes, bytearray)):
        _raise_unsupported_media()
    if isinstance(value, Sequence):
        return tuple(_image_attachment_from_value(item, source_path=source_path) for item in value)
    _raise_unsupported_media()


def _image_attachment_from_value(value: Any, *, source_path: str) -> ImageAttachment:
    if isinstance(value, Mapping):
        return _image_attachment_from_mapping(value, source_path=source_path)
    if isinstance(value, str):
        return _image_attachment_from_string(value, source_path=source_path)
    if isinstance(value, (bytes, bytearray)):
        _raise_unsupported_media()
    _raise_unsupported_media()


def _image_attachment_from_mapping(
    item: Mapping[str, Any],
    *,
    source_path: str,
) -> ImageAttachment:
    content_type = _content_type(item)
    if content_type is not None and content_type not in IMAGE_CONTENT_TYPES:
        if content_type in MEDIA_CONTENT_TYPES or content_type not in TEXT_CONTENT_TYPES:
            _raise_unsupported_media()

    explicit_mime = _normalize_image_mime(_first_text((item, IMAGE_MIME_KEYS)))
    filename = _attachment_filename(item)

    for key in IMAGE_DATA_KEYS:
        if key in item:
            return _image_attachment_from_string(
                item.get(key),
                source_path=f"{source_path}.{key}",
                explicit_mime=explicit_mime,
                filename=filename,
            )
    for key in IMAGE_URL_KEYS:
        if key in item:
            return _image_attachment_from_url_value(
                item.get(key),
                source_path=f"{source_path}.{key}",
                explicit_mime=explicit_mime,
                filename=filename,
            )
    for key in IMAGE_NESTED_KEYS:
        if key in item:
            nested_source_path = f"{source_path}.{key}"
            nested = item.get(key)
            if isinstance(nested, Mapping):
                merged = {**item, **nested}
                return _image_attachment_from_mapping(merged, source_path=nested_source_path)
            return _image_attachment_from_value(nested, source_path=nested_source_path)

    _raise_unsupported_media()


def _image_attachment_from_url_value(
    value: Any,
    *,
    source_path: str,
    explicit_mime: str | None = None,
    filename: str | None = None,
) -> ImageAttachment:
    if isinstance(value, Mapping):
        nested_mime = _normalize_image_mime(_first_text((value, IMAGE_MIME_KEYS))) or explicit_mime
        nested_filename = _attachment_filename(value) or filename
        url = _first_text((value, ("url", "uri", "image_url")))
        if url is None:
            _raise_unsupported_media()
        return _image_attachment_from_string(
            url,
            source_path=f"{source_path}.url",
            explicit_mime=nested_mime,
            filename=nested_filename,
        )
    return _image_attachment_from_string(
        value,
        source_path=source_path,
        explicit_mime=explicit_mime,
        filename=filename,
    )


def _image_attachment_from_string(
    value: Any,
    *,
    source_path: str,
    explicit_mime: str | None = None,
    filename: str | None = None,
) -> ImageAttachment:
    if not isinstance(value, str):
        _raise_unsupported_media()
    text = value.strip()
    if not text:
        _raise_unsupported_media()

    data_url = _parse_image_data_url(text)
    if data_url is not None:
        mime_type, data = data_url
        return _image_attachment_from_bytes(
            data,
            source_path=source_path,
            mime_type=explicit_mime or mime_type,
            filename=filename,
        )

    if _looks_like_url(text):
        return _image_attachment_from_url(
            text,
            source_path=source_path,
            explicit_mime=explicit_mime,
            filename=filename,
        )

    if explicit_mime is None:
        _raise_unsupported_media()
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        _raise_unsupported_media()
    return _image_attachment_from_bytes(
        data,
        source_path=source_path,
        mime_type=explicit_mime,
        filename=filename,
    )


def _image_attachment_from_bytes(
    data: bytes,
    *,
    source_path: str,
    mime_type: str,
    filename: str | None,
) -> ImageAttachment:
    normalized_mime = _normalize_image_mime(mime_type)
    if normalized_mime is None:
        _raise_unsupported_media()
    extension = _attachment_extension(filename, normalized_mime)
    return ImageAttachment(
        mime_type=normalized_mime,
        source_field=source_path,
        filename=filename,
        extension=extension,
        size_bytes=len(data),
        content_hash=_short_sha256(data),
        source_hash=None,
        data=data,
        url=None,
    )


def _image_attachment_from_url(
    url: str,
    *,
    source_path: str,
    explicit_mime: str | None,
    filename: str | None,
) -> ImageAttachment:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        _raise_unsupported_media()
    inferred_filename = filename or _url_filename(parsed.path)
    mime_type = _normalize_image_mime(explicit_mime or _guess_mime_from_filename(inferred_filename))
    if mime_type is None:
        _raise_unsupported_media()
    return ImageAttachment(
        mime_type=mime_type,
        source_field=source_path,
        filename=inferred_filename,
        extension=_attachment_extension(inferred_filename, mime_type),
        size_bytes=0,
        content_hash=None,
        source_hash=_short_sha256(url.encode("utf-8")),
        data=None,
        url=url,
    )


def _parse_image_data_url(value: str) -> tuple[str, bytes] | None:
    if not value.lower().startswith("data:"):
        return None
    header, separator, encoded = value.partition(",")
    if not separator:
        _raise_unsupported_media()
    parts = header[5:].split(";")
    mime_type = _normalize_image_mime(parts[0] if parts else "")
    if mime_type is None or "base64" not in {part.lower() for part in parts[1:]}:
        _raise_unsupported_media()
    try:
        return mime_type, base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        _raise_unsupported_media()


def _looks_like_image_data_url(value: str) -> bool:
    return value.strip().lower().startswith("data:image/")


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.netloc)


def _normalize_image_mime(value: str | None) -> str | None:
    if value is None:
        return None
    mime_type = value.split(";", 1)[0].strip().lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    return mime_type if mime_type in SUPPORTED_IMAGE_MIME_TYPES else None


def _attachment_filename(item: Mapping[str, Any]) -> str | None:
    filename = _first_text((item, IMAGE_FILENAME_KEYS))
    if filename is None:
        return None
    name = unquote(filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip()
    return name[:120] or None


def _url_filename(path: str) -> str | None:
    if not path:
        return None
    name = unquote(path.rsplit("/", 1)[-1]).strip()
    return name[:120] or None


def _attachment_extension(filename: str | None, mime_type: str) -> str | None:
    if filename and "." in filename:
        extension = filename.rsplit(".", 1)[-1].strip().lower()
        if extension == "jpeg":
            return "jpg"
        return extension[:16] or None
    return IMAGE_MIME_EXTENSIONS.get(mime_type)


def _guess_mime_from_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    mime_type, _encoding = mimetypes.guess_type(filename)
    return mime_type


def _short_sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def _raise_unsupported_media() -> None:
    raise PayloadParseError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
