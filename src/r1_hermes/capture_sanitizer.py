from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .payloads import (
    PayloadParseError,
    parse_chat_history_params,
    parse_chat_send_params,
    parse_connect_params,
    request_params,
)

DUMMY_GATEWAY_TOKEN = "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"  # noqa: S105 - public fixture sentinel
DUMMY_DEVICE_TOKEN = "DUMMY_DEVICE_TOKEN_DO_NOT_USE"  # noqa: S105 - public fixture sentinel
SANITIZED_DEVICE_ID = "r1-sanitized-device"
SANITIZED_CLIENT_NAME = "Rabbit R1 sanitized fixture"
SANITIZED_MESSAGE_TEXT = "hello Hermes from sanitized fixture"
SANITIZED_ASSISTANT_TEXT = "sanitized assistant response"
SANITIZED_SESSION_KEY = "sanitized-session"
SANITIZED_RUN_ID = "sanitized-run-001"
SANITIZED_FRAME_ID = "sanitized-frame-001"
SANITIZED_TIMESTAMP_MS = 1710000000000
DUMMY_BINARY_DATA = "DUMMY_BINARY_DATA_OMITTED"
DUMMY_IMAGE_BASE64 = "cjEtaW1hZ2U="
PUBLIC_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "p9sAAAAASUVORK5CYII="
)
SAFE_TAILSCALE_IP = "100.64.0.10"
SAFE_TEST_NET_IP = "192.0.2.10"
SAFE_WS_URL = f"ws://{SAFE_TAILSCALE_IP}:18789/"
SAFE_NETWORK_VALUES = {SAFE_TAILSCALE_IP, SAFE_TEST_NET_IP, SAFE_WS_URL}
REDACTED = "[REDACTED]"

CONNECT_METHODS = {"connect", "gateway.connect"}
REQUEST_METHODS = CONNECT_METHODS | {"chat.send", "chat.history"}
EVENT_NAMES = {"connect.challenge", "connect.ok", "node.pair.approved", "chat"}
GATEWAY_TOKEN_KEYS = {"token", "authToken", "gatewayToken", "bearerToken"}
DEVICE_TOKEN_KEYS = {"deviceToken"}
AUTH_HEADER_KEYS = {"authorization", "authHeader", "auth_header", "bearer", "bearerTokenHeader"}
DEVICE_ID_KEYS = {"deviceId", "device_id", "serial", "serialNumber"}
DEVICE_CONTEXT_KEYS = {"device"}
CLIENT_CONTEXT_KEYS = {"client"}
SESSION_CONTEXT_KEYS = {"session", "conversation"}
PRIVATE_NAME_KEYS = {"displayName", "display_name", "clientName", "client_name", "name", "model"}
MESSAGE_TEXT_KEYS = {"message", "text", "body", "prompt", "input"}
BINARY_DATA_KEYS = {"data", "audio", "image", "payloadBytes", "payload_bytes"}
SESSION_KEYS = {"sessionKey", "session_key", "sessionId", "session_id", "conversationId"}
RUN_ID_KEYS = {"idempotencyKey", "idempotency_key", "requestId", "request_id", "runId"}
NETWORK_KEYS = {"host", "hostname", "ip", "address", "url", "uri", "endpoint"}
NETWORK_LIST_KEYS = {"hosts", "ips", "addresses", "endpoints"}
TIMESTAMP_KEYS = {"ts", "timestamp", "createdAt", "created_at", "updatedAt", "updated_at"}
PRIVATE_MARKERS = (
    "private",
    "secret",
    "real-device",
    "raw-",
    "bearer ",
    "authorization:",
)


class CaptureSchemaError(ValueError):
    """Raised when a public capture fixture is not safe or schema-compatible."""


@dataclass
class _SanitizeState:
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)

    def alias(self, namespace: str, raw: Any, base: str) -> str:
        key = str(raw or "")
        if not key:
            return base
        namespace_aliases = self.aliases.setdefault(namespace, {})
        existing = namespace_aliases.get(key)
        if existing is not None:
            return existing
        suffix = "" if not namespace_aliases else f"-{len(namespace_aliases) + 1}"
        value = f"{base}{suffix}"
        namespace_aliases[key] = value
        return value


@dataclass(frozen=True)
class _Context:
    method: str = ""
    event: str = ""
    parent_key: str = ""
    role: str = ""

    def child(self, *, key: str, value: Any) -> "_Context":
        method = (
            str(value.get("method") or self.method)
            if isinstance(value, Mapping)
            else self.method
        )
        event = str(value.get("event") or self.event) if isinstance(value, Mapping) else self.event
        role = str(value.get("role") or self.role) if isinstance(value, Mapping) else self.role
        return _Context(method=method, event=event, parent_key=key, role=role)


def sanitize_capture(value: Any) -> Any:
    """Return a commit-safe copy of a Rabbit R1/OpenClaw capture.

    The sanitizer preserves request/response/event shape while replacing bearer secrets,
    device identifiers, prompt text, assistant text, session/run identifiers, and network
    addresses with deterministic public fixture values.
    """
    return _sanitize(value, state=_SanitizeState(), context=_Context())


def validate_sanitized_capture(
    value: Any,
    *,
    forbidden_values: Iterable[str] = (),
) -> None:
    """Validate a sanitized fixture and fail closed on known private material."""
    forbidden = tuple(item for item in forbidden_values if item)
    _scan_for_private_material(value, forbidden_values=forbidden, path="$")
    for frame in _iter_fixture_frames(value):
        _validate_frame_schema(frame)


def _sanitize(value: Any, *, state: _SanitizeState, context: _Context) -> Any:
    if isinstance(value, Mapping):
        frame_context = context.child(key=context.parent_key, value=value)
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            sanitized[key] = _sanitize_child(key, child, state=state, context=frame_context)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item, state=state, context=context) for item in value]
    return value


def _sanitize_child(key: str, child: Any, *, state: _SanitizeState, context: _Context) -> Any:
    if key in GATEWAY_TOKEN_KEYS:
        return DUMMY_GATEWAY_TOKEN
    if key in DEVICE_TOKEN_KEYS:
        return DUMMY_DEVICE_TOKEN
    if key in AUTH_HEADER_KEYS or key.lower() in AUTH_HEADER_KEYS:
        return REDACTED
    if _is_device_identifier_field(key, child, context):
        return state.alias("device", child, SANITIZED_DEVICE_ID)
    if key in PRIVATE_NAME_KEYS and context.parent_key in DEVICE_CONTEXT_KEYS | CLIENT_CONTEXT_KEYS:
        return SANITIZED_CLIENT_NAME
    if key in NETWORK_LIST_KEYS and isinstance(child, list):
        return _sanitize_network_list(child)
    if key in NETWORK_KEYS and isinstance(child, str):
        return SAFE_WS_URL if key in {"url", "uri", "endpoint"} else SAFE_TAILSCALE_IP
    if key in SESSION_KEYS and isinstance(child, str):
        return state.alias("session", child, SANITIZED_SESSION_KEY)
    if key in {"id", "key"} and context.parent_key in SESSION_CONTEXT_KEYS:
        return state.alias("session", child, SANITIZED_SESSION_KEY)
    if key in RUN_ID_KEYS and isinstance(child, str):
        return state.alias("run", child, SANITIZED_RUN_ID)
    if key in TIMESTAMP_KEYS and isinstance(child, (int, float, str)):
        return SANITIZED_TIMESTAMP_MS
    if key == "id" and context.parent_key not in DEVICE_CONTEXT_KEYS and isinstance(child, str):
        return state.alias("frame", child, SANITIZED_FRAME_ID)
    if key in MESSAGE_TEXT_KEYS and isinstance(child, str):
        return _sanitized_text_for_context(context)
    if key in BINARY_DATA_KEYS and isinstance(child, str):
        if child.strip().lower().startswith("data:image/"):
            return f"data:image/jpeg;base64,{DUMMY_BINARY_DATA}"
        return DUMMY_BINARY_DATA
    return _sanitize(child, state=state, context=context.child(key=key, value=child))


def _is_device_identifier_field(key: str, child: Any, context: _Context) -> bool:
    if key in DEVICE_ID_KEYS:
        return isinstance(child, str)
    if key == "id" and context.parent_key in DEVICE_CONTEXT_KEYS:
        return isinstance(child, str)
    return key in DEVICE_CONTEXT_KEYS and isinstance(child, str)


def _sanitize_network_list(values: list[Any]) -> list[Any]:
    replacements = [SAFE_TAILSCALE_IP, SAFE_TEST_NET_IP]
    sanitized: list[Any] = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            sanitized.append(replacements[min(index, len(replacements) - 1)])
        else:
            sanitized.append(value)
    return sanitized


def _sanitized_text_for_context(context: _Context) -> str:
    if context.event == "chat" or context.role == "assistant":
        return SANITIZED_ASSISTANT_TEXT
    return SANITIZED_MESSAGE_TEXT


def _iter_fixture_frames(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, list):
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise CaptureSchemaError(f"frame at index {index} must be an object")
            yield item
        return
    if isinstance(value, Mapping):
        frames = value.get("frames")
        if isinstance(frames, list):
            yield from _iter_fixture_frames(frames)
            return
        yield value
        return
    raise CaptureSchemaError("fixture must be a JSON object or array of objects")


def _validate_frame_schema(frame: Mapping[str, Any]) -> None:
    frame_type = frame.get("type")
    if frame_type == "clawdbot-gateway":
        _validate_qr_payload(frame)
        return
    if frame_type == "req":
        _validate_request_frame(frame)
        return
    if frame_type == "res":
        _validate_response_frame(frame)
        return
    if frame_type == "event":
        _validate_event_frame(frame)
        return
    raise CaptureSchemaError("frame type must be req, res, event, or clawdbot-gateway")


def _validate_qr_payload(frame: Mapping[str, Any]) -> None:
    if frame.get("version") != 1:
        raise CaptureSchemaError("QR fixture version must be 1")
    if frame.get("token") != DUMMY_GATEWAY_TOKEN:
        raise CaptureSchemaError("QR token must use a public dummy value")
    if frame.get("protocol") not in {"ws", "wss"}:
        raise CaptureSchemaError("QR protocol must be ws or wss")
    if not isinstance(frame.get("port"), int):
        raise CaptureSchemaError("QR port must be an integer")
    ips = frame.get("ips")
    if not isinstance(ips, list) or not all(isinstance(item, str) for item in ips):
        raise CaptureSchemaError("QR ips must be a string array")


def _validate_request_frame(frame: Mapping[str, Any]) -> None:
    method = frame.get("method")
    if method not in REQUEST_METHODS:
        raise CaptureSchemaError("request fixture method is not in the replay schema")
    if not isinstance(frame.get("id"), str):
        raise CaptureSchemaError("request fixture id must be a string")
    try:
        params = request_params(frame)
    except PayloadParseError as exc:
        raise CaptureSchemaError(exc.message) from exc
    if method in CONNECT_METHODS:
        _validate_connect_request(params)
    elif method == "chat.send":
        _validate_chat_request(params)
    elif method == "chat.history":
        _validate_chat_history_request(params)


def _validate_connect_request(params: Mapping[str, Any]) -> None:
    try:
        connect = parse_connect_params(params)
    except PayloadParseError as exc:
        raise CaptureSchemaError(exc.message) from exc
    if connect.auth_token != DUMMY_GATEWAY_TOKEN:
        raise CaptureSchemaError("auth token must use a public dummy value")
    _require_public_device_id(connect.device_id)
    _require_public_text(connect.display_name, label="display name")


def _validate_chat_request(params: Mapping[str, Any]) -> None:
    try:
        chat = parse_chat_send_params(params)
    except PayloadParseError as exc:
        if exc.code == "UNSUPPORTED_MEDIA":
            _validate_unsupported_media_chat_request(params)
            return
        raise CaptureSchemaError(exc.message) from exc
    if chat.message:
        _require_public_text(chat.message, label="chat message")
    elif not chat.attachments:
        raise CaptureSchemaError("chat message is required")
    _require_public_text(chat.session_key, label="session key")
    if chat.idempotency_key is not None:
        _require_public_text(chat.idempotency_key, label="run id")


def _validate_unsupported_media_chat_request(params: Mapping[str, Any]) -> None:
    try:
        history = parse_chat_history_params(params)
    except PayloadParseError as exc:
        raise CaptureSchemaError(exc.message) from exc
    _require_public_text(history.session_key, label="session key")
    for key in RUN_ID_KEYS:
        run_id = params.get(key)
        if isinstance(run_id, str):
            _require_public_text(run_id, label="run id")


def _validate_chat_history_request(params: Mapping[str, Any]) -> None:
    try:
        history = parse_chat_history_params(params)
    except PayloadParseError as exc:
        raise CaptureSchemaError(exc.message) from exc
    _require_public_text(history.session_key, label="session key")


def _validate_response_frame(frame: Mapping[str, Any]) -> None:
    if "ok" in frame and not isinstance(frame.get("ok"), bool):
        raise CaptureSchemaError("response ok must be a boolean")
    if "id" in frame and frame.get("id") is not None and not isinstance(frame.get("id"), str):
        raise CaptureSchemaError("response id must be a string or null")


def _validate_event_frame(frame: Mapping[str, Any]) -> None:
    event = frame.get("event")
    if event not in EVENT_NAMES:
        raise CaptureSchemaError("event fixture name is not in the replay schema")
    payload = frame.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise CaptureSchemaError("event payload must be an object")
    device_id = payload.get("deviceId") or payload.get("device_id")
    if isinstance(device_id, str):
        _require_public_device_id(device_id)
    if event == "chat":
        state = payload.get("state")
        if state is not None and not isinstance(state, str):
            raise CaptureSchemaError("chat event state must be a string")
        message = payload.get("message")
        if message is not None and not isinstance(message, Mapping):
            raise CaptureSchemaError("chat event message must be an object")


def _scan_for_private_material(value: Any, *, forbidden_values: tuple[str, ...], path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            _validate_secret_field(key, child, path=child_path)
            _scan_for_private_material(
                child,
                forbidden_values=forbidden_values,
                path=child_path,
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_for_private_material(
                item,
                forbidden_values=forbidden_values,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(value, str):
        _require_no_forbidden_text(value, forbidden_values=forbidden_values, path=path)


def _validate_secret_field(key: str, value: Any, *, path: str) -> None:
    if key in GATEWAY_TOKEN_KEYS and value not in {DUMMY_GATEWAY_TOKEN, REDACTED}:
        raise CaptureSchemaError("auth token must use a public dummy value")
    if key in DEVICE_TOKEN_KEYS and value not in {DUMMY_DEVICE_TOKEN, REDACTED}:
        raise CaptureSchemaError("device token must use a public dummy value")
    if key in AUTH_HEADER_KEYS or key.lower() in AUTH_HEADER_KEYS:
        if value != REDACTED:
            raise CaptureSchemaError("raw authorization headers must be redacted")
    if key in DEVICE_ID_KEYS and isinstance(value, str):
        _require_public_device_id(value)
    if key in NETWORK_KEYS and isinstance(value, str):
        _require_public_network_value(value)
    if key in NETWORK_LIST_KEYS and isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                _require_public_network_value(item)
    if (
        key in BINARY_DATA_KEYS
        and isinstance(value, str)
        and not _is_public_binary_placeholder(value)
    ):
        raise CaptureSchemaError("binary capture data must be replaced with a dummy placeholder")
    if key in TIMESTAMP_KEYS and value != SANITIZED_TIMESTAMP_MS:
        raise CaptureSchemaError("capture timestamps must use the sanitized fixture timestamp")
    del path


def _require_no_forbidden_text(
    text: str,
    *,
    forbidden_values: tuple[str, ...],
    path: str,
) -> None:
    for forbidden in forbidden_values:
        if forbidden and forbidden in text:
            raise CaptureSchemaError(f"forbidden private value remains at {path}")
    lowered = text.lower()
    if lowered.startswith("bearer ") or "authorization:" in lowered:
        raise CaptureSchemaError(f"raw authorization material remains at {path}")


def _require_public_device_id(device_id: str) -> None:
    if not device_id.startswith("r1-"):
        raise CaptureSchemaError("device id must use a public r1-* fixture alias")
    _require_public_text(device_id, label="device id")


def _require_public_text(text: str, *, label: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in PRIVATE_MARKERS):
        raise CaptureSchemaError(f"{label} still looks private")


def _require_public_network_value(value: str) -> None:
    if value not in SAFE_NETWORK_VALUES:
        raise CaptureSchemaError("network values must use documentation-safe fixture addresses")


def _is_public_binary_placeholder(value: str) -> bool:
    if value == DUMMY_BINARY_DATA or (value.startswith("DUMMY_") and value.endswith("_OMITTED")):
        return True
    if value.startswith("data:image/"):
        _media_type, separator, encoded = value.partition(",")
        encoded = encoded.strip()
        return bool(
            separator
            and (
                encoded in {DUMMY_IMAGE_BASE64, PUBLIC_TINY_PNG_BASE64}
                or _is_public_binary_placeholder(encoded)
            )
        )
    return False


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, value: Any, *, overwrite: bool) -> Path:
    if path.exists() and not overwrite:
        raise SystemExit(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w") as handle:
            fd = -1
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    finally:
        if fd != -1:  # pragma: no cover - defensive cleanup
            os.close(fd)
    tmp_path.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m r1_hermes.capture_sanitizer",
        description="Sanitize private Rabbit R1/OpenClaw captures into public replay fixtures.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Private or sanitized JSON capture",
    )
    parser.add_argument("--output", type=Path, help="Public sanitized fixture output path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate --input as an already sanitized public fixture without writing output",
    )
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        help="Private value that must not appear in the sanitized output; repeat as needed",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing --output file",
    )
    args = parser.parse_args(argv)

    raw = _read_json(args.input)
    try:
        if args.check:
            validate_sanitized_capture(raw, forbidden_values=args.forbid)
            print(f"Sanitized fixture is valid: {args.input}")
            return
        if args.output is None:
            raise SystemExit("--output is required unless --check is set")
        sanitized = sanitize_capture(raw)
        validate_sanitized_capture(sanitized, forbidden_values=args.forbid)
    except CaptureSchemaError as exc:
        raise SystemExit(str(exc)) from exc

    path = _write_json(args.output, sanitized, overwrite=args.overwrite)
    print(f"Wrote sanitized fixture: {path}")


if __name__ == "__main__":
    main()
