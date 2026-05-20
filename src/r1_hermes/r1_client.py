from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession, WSMsgType


class R1ProbeError(RuntimeError):
    """Raised when a Rabbit R1/OpenClaw probe flow fails."""


@dataclass(frozen=True)
class R1ProbeResult:
    connected: bool
    device_token: str
    run_id: str
    response_text: str
    raw_ack: dict[str, Any]
    raw_event: dict[str, Any]


@dataclass(frozen=True)
class R1ProbeClient:
    """Small Rabbit R1/OpenClaw-compatible client used for end-to-end smoke tests."""

    url: str
    token: str
    device_id: str = "r1-probe"
    timeout_seconds: float = 30
    client_name: str = "r1-hermes probe"

    async def send_message(self, message: str, *, session_key: str = "main") -> R1ProbeResult:
        timeout = aiohttp_timeout(self.timeout_seconds)
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(self.url) as ws:
                challenge = await self._receive_json(ws, expected="connect.challenge")
                if (
                    challenge.get("type") != "event"
                    or challenge.get("event") != "connect.challenge"
                ):
                    raise R1ProbeError("expected connect.challenge event")

                await ws.send_json(
                    {
                        "type": "req",
                        "id": "connect-1",
                        "method": "connect",
                        "params": {
                            "auth": {"token": self.token},
                            "device": {"id": self.device_id},
                            "client": {"displayName": self.client_name},
                        },
                    }
                )
                hello = await self._receive_json(ws)
                if not hello.get("ok"):
                    raise R1ProbeError(_error_text(hello))
                device_token = str(
                    ((hello.get("payload") or {}).get("auth") or {}).get("deviceToken") or ""
                )
                if not device_token:
                    raise R1ProbeError("connect response did not include deviceToken")

                chat_id = f"chat-{secrets.token_hex(4)}"
                run_id = f"probe-{secrets.token_hex(8)}"
                await ws.send_json(
                    {
                        "type": "req",
                        "id": chat_id,
                        "method": "chat.send",
                        "params": {
                            "message": message,
                            "sessionKey": session_key,
                            "idempotencyKey": run_id,
                        },
                    }
                )
                ack = await self._receive_json(ws)
                if not ack.get("ok"):
                    raise R1ProbeError(_error_text(ack))
                event = await self._receive_chat_event(ws)
                response_text = _extract_response_text(event)
                return R1ProbeResult(
                    connected=True,
                    device_token=device_token,
                    run_id=str((ack.get("payload") or {}).get("runId") or run_id),
                    response_text=response_text,
                    raw_ack=ack,
                    raw_event=event,
                )

    async def _receive_chat_event(self, ws) -> dict[str, Any]:
        while True:
            frame = await self._receive_json(ws)
            if frame.get("type") == "event" and frame.get("event") == "chat":
                payload = frame.get("payload") or {}
                state = str(payload.get("state") or "")
                if state == "error":
                    raise R1ProbeError(_chat_event_error_text(frame))
                if state in {"final", ""}:
                    return frame

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


def aiohttp_timeout(seconds: float):
    from aiohttp import ClientTimeout

    return ClientTimeout(total=seconds, sock_connect=seconds, sock_read=seconds)


def _error_text(frame: dict[str, Any]) -> str:
    error = frame.get("error") or {}
    code = str(error.get("code") or "ERROR")
    message = str(error.get("message") or "request failed")
    return f"{code}: {message}"


def _chat_event_error_text(frame: dict[str, Any]) -> str:
    payload = frame.get("payload") or {}
    error = payload.get("error") or {}
    code = str(error.get("code") or "CHAT_RUN_FAILED")
    message = str(error.get("message") or "chat run failed")
    return f"{code}: {message}"


def _extract_response_text(event: dict[str, Any]) -> str:
    message = (event.get("payload") or {}).get("message") or {}
    content = message.get("content") or []
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)
