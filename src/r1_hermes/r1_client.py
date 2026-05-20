from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession, WSMsgType

CONNECT_METHODS = {"connect", "gateway.connect"}
CONNECT_ACK_EVENTS = {"connect.ok", "node.pair.approved"}
AUTH_SECRET_KEYS = {"token", "authToken", "gatewayToken", "deviceToken", "bearerToken"}
CONTENT_SECRET_KEYS = {
    "message",
    "text",
    "body",
    "prompt",
    "input",
    "data",
    "base64",
    "b64_json",
    "bytes",
    "blob",
    "audio",
    "audio_url",
    "image",
    "image_url",
    "media",
    "url",
    "uri",
}
DEVICE_SECRET_KEYS = {"deviceId", "device_id", "serial", "serialNumber"}
DEVICE_CONTEXT_KEYS = {"device"}
REDACTED = "[REDACTED]"
PUBLIC_DUMMY_SECRET_PREFIXES = (
    "DUMMY_GATEWAY_TOKEN_",
    "DUMMY_DEVICE_TOKEN_",
    "DUMMY_AUDIO_",
    "DUMMY_IMAGE_",
)


class R1ProbeError(RuntimeError):
    """Raised when a Rabbit R1/OpenClaw probe flow fails."""


@dataclass(frozen=True)
class R1ProbeResult:
    connected: bool
    device_token: str = field(repr=False)
    run_id: str
    response_text: str
    raw_ack: dict[str, Any] = field(repr=False)
    raw_event: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class R1ProbeClient:
    """Small Rabbit R1/OpenClaw-compatible client used for end-to-end smoke tests."""

    url: str
    token: str
    device_id: str = "r1-probe"
    timeout_seconds: float = 30
    client_name: str = "r1-hermes probe"
    connect_method: str = "connect"
    dump_frames: bool = False
    frame_sink: Callable[[str], None] | None = None

    async def send_message(self, message: str, *, session_key: str = "main") -> R1ProbeResult:
        if self.connect_method not in CONNECT_METHODS:
            raise R1ProbeError("unsupported connect method")

        timeout = aiohttp_timeout(self.timeout_seconds)
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.url) as ws:
                challenge = await self._receive_json(ws, expected="connect.challenge")
                self._dump_frame("recv", challenge)
                if (
                    challenge.get("type") != "event"
                    or challenge.get("event") != "connect.challenge"
                ):
                    raise R1ProbeError("expected connect.challenge event")

                connect_frame = {
                    "type": "req",
                    "id": "connect-1",
                    "method": self.connect_method,
                    "params": {
                        "auth": {"token": self.token},
                        "device": {"id": self.device_id},
                        "client": {"displayName": self.client_name},
                    },
                }
                self._dump_frame("send", connect_frame)
                await ws.send_json(connect_frame)
                hello = await self._receive_response(
                    ws, expected="connect response", redact_response=False
                )
                if not hello.get("ok"):
                    self._dump_frame("recv", hello)
                    raise R1ProbeError(_error_text(hello, secret_values=(self.token,)))
                device_token = str(
                    ((hello.get("payload") or {}).get("auth") or {}).get("deviceToken") or ""
                )
                if not device_token:
                    self._dump_frame("recv", hello)
                    raise R1ProbeError("connect response did not include deviceToken")
                self._dump_frame("recv", hello, secret_values=(device_token,))

                chat_id = f"chat-{secrets.token_hex(4)}"
                run_id = f"probe-{secrets.token_hex(8)}"
                chat_frame = {
                    "type": "req",
                    "id": chat_id,
                    "method": "chat.send",
                    "params": {
                        "message": message,
                        "sessionKey": session_key,
                        "idempotencyKey": run_id,
                    },
                }
                self._dump_frame("send", chat_frame)
                await ws.send_json(chat_frame)
                ack = await self._receive_response(
                    ws,
                    expected="chat.send acknowledgement",
                    secret_values=(device_token,),
                )
                if not ack.get("ok"):
                    raise R1ProbeError(_error_text(ack, secret_values=(self.token, device_token)))
                event = await self._receive_chat_event(ws, secret_values=(device_token,))
                response_text = _extract_response_text(event)
                return R1ProbeResult(
                    connected=True,
                    device_token=device_token,
                    run_id=str((ack.get("payload") or {}).get("runId") or run_id),
                    response_text=response_text,
                    raw_ack=ack,
                    raw_event=event,
                )

    async def _receive_chat_event(
        self, ws, *, secret_values: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        while True:
            frame = await self._receive_json(ws)
            self._dump_frame("recv", frame, secret_values=secret_values)
            if frame.get("type") == "event" and frame.get("event") == "chat":
                payload = frame.get("payload") or {}
                state = str(payload.get("state") or "")
                if state == "error":
                    raise R1ProbeError(
                        _chat_event_error_text(
                            frame, secret_values=(self.token, *secret_values)
                        )
                    )
                if state in {"final", ""}:
                    return frame

    async def _receive_response(
        self,
        ws,
        *,
        expected: str,
        secret_values: tuple[str, ...] = (),
        redact_response: bool = True,
    ) -> dict[str, Any]:
        while True:
            frame = await self._receive_json(ws, expected=expected)
            if frame.get("type") == "res":
                if redact_response:
                    self._dump_frame("recv", frame, secret_values=secret_values)
                return frame
            self._dump_frame("recv", frame, secret_values=secret_values)
            if frame.get("type") == "event" and frame.get("event") in CONNECT_ACK_EVENTS:
                continue
            raise R1ProbeError(f"expected {expected}")

    async def _receive_json(self, ws, *, expected: str = "gateway response") -> dict[str, Any]:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise R1ProbeError("timed out waiting for gateway response") from exc
        if msg.type == WSMsgType.TEXT:
            data = msg.json()
            if isinstance(data, dict):
                return data
            raise R1ProbeError("gateway sent non-object JSON")
        if msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}:
            raise R1ProbeError(f"gateway closed WebSocket before {expected}")
        if msg.type == WSMsgType.ERROR:
            raise R1ProbeError(f"WebSocket error: {ws.exception()}")
        raise R1ProbeError(f"unexpected WebSocket message type: {msg.type}")

    def _dump_frame(
        self, direction: str, frame: dict[str, Any], *, secret_values: tuple[str, ...] = ()
    ) -> None:
        if not self.dump_frames:
            return
        sink = self.frame_sink or print
        safe = redact_frame_secrets(frame, secret_values=(self.token, *secret_values))
        sink(f"{direction} {json.dumps(safe, sort_keys=True, separators=(',', ':'))}")


def redact_frame_secrets(value: Any, *, secret_values: tuple[str, ...] = ()) -> Any:
    return _redact_frame_secrets(value, secret_values=secret_values, parent_key="")


def _redact_frame_secrets(
    value: Any, *, secret_values: tuple[str, ...] = (), parent_key: str = ""
) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if (
                key in AUTH_SECRET_KEYS
                or key in DEVICE_SECRET_KEYS
                or (key == "id" and parent_key in DEVICE_CONTEXT_KEYS)
                or (key in DEVICE_CONTEXT_KEYS and isinstance(child, str))
                or (key in CONTENT_SECRET_KEYS and isinstance(child, str))
            ):
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact_frame_secrets(
                    child, secret_values=secret_values, parent_key=key
                )
        return redacted
    if isinstance(value, list):
        return [
            _redact_frame_secrets(item, secret_values=secret_values, parent_key=parent_key)
            for item in value
        ]
    if isinstance(value, str):
        return _redact_text(value, secret_values=secret_values)
    return value


def _redact_text(text: str, *, secret_values: tuple[str, ...]) -> str:
    for secret in secret_values:
        if secret:
            text = text.replace(secret, REDACTED)
    for prefix in PUBLIC_DUMMY_SECRET_PREFIXES:
        if prefix in text:
            words = text.split()
            text = " ".join(REDACTED if word.startswith(prefix) else word for word in words)
    return text


def aiohttp_timeout(seconds: float):
    from aiohttp import ClientTimeout

    return ClientTimeout(total=seconds, sock_connect=seconds, sock_read=seconds)


def _error_text(frame: dict[str, Any], *, secret_values: tuple[str, ...] = ()) -> str:
    error = frame.get("error") or {}
    code = str(error.get("code") or "ERROR")
    message = _redact_text(
        str(error.get("message") or "request failed"), secret_values=secret_values
    )
    return f"{code}: {message}"


def _chat_event_error_text(frame: dict[str, Any], *, secret_values: tuple[str, ...] = ()) -> str:
    payload = frame.get("payload") or {}
    error = payload.get("error") or {}
    code = str(error.get("code") or "CHAT_RUN_FAILED")
    message = _redact_text(
        str(error.get("message") or "chat run failed"), secret_values=secret_values
    )
    return f"{code}: {message}"


def _extract_response_text(event: dict[str, Any]) -> str:
    message = (event.get("payload") or {}).get("message") or {}
    content = message.get("content") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)
