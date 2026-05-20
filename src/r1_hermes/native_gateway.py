from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from .adapter import R1HermesAdapter, R1HermesConfig
from .toolsets import sanitize_platform_toolsets

DEFAULT_NATIVE_PLATFORM = "rabbit_r1"
DEFAULT_NATIVE_TOOLSETS = ("safe",)


GatewayMessageHandler = Callable[["R1GatewayMessageEvent"], Awaitable[Any]]


@dataclass(frozen=True)
class R1GatewayMessageEvent:
    """Minimal Hermes Gateway MessageEvent-compatible shape for the R1 prototype.

    Hermes proper owns the real MessageEvent class. This local dataclass is intentionally
    narrow and dependency-free so r1-hermes can test the security boundary and conversion
    semantics without importing a second repository.
    """

    platform: str
    user_id: str
    session_id: str
    source: str
    text: str = field(repr=False)
    channel_id: str | None = None
    message_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class R1GatewayMessageBridge:
    """Convert authenticated R1 chat messages into gateway-style message events."""

    def __init__(
        self,
        *,
        gateway_message_handler: GatewayMessageHandler,
        platform_name: str = DEFAULT_NATIVE_PLATFORM,
        platform_toolsets: Sequence[str] = DEFAULT_NATIVE_TOOLSETS,
        allow_high_impact_toolsets: bool = False,
    ):
        self.gateway_message_handler = gateway_message_handler
        self.platform_name = _safe_component(platform_name or DEFAULT_NATIVE_PLATFORM)
        self.platform_toolsets = sanitize_platform_toolsets(
            platform_toolsets,
            allow_high_impact_toolsets=allow_high_impact_toolsets,
        )

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        event = self.to_message_event(text, device_id=device_id, session_key=session_key)
        result = await self.gateway_message_handler(event)
        return _reply_text(result)

    def to_message_event(
        self,
        text: str,
        *,
        device_id: str,
        session_key: str,
        message_id: str | None = None,
    ) -> R1GatewayMessageEvent:
        safe_device_id = _safe_component(device_id)
        safe_session_key = self.normalize_session_key(session_key)
        source = f"{self.platform_name}:{safe_device_id}:{safe_session_key}"
        session_id = f"r1:{safe_device_id}:{safe_session_key}"
        return R1GatewayMessageEvent(
            platform=self.platform_name,
            user_id=safe_device_id,
            session_id=session_id,
            source=source,
            text=text,
            channel_id=safe_device_id,
            message_id=message_id,
            metadata={
                "device_id": safe_device_id,
                "session_key": safe_session_key,
                "platform_toolsets": self.platform_toolsets,
            },
        )

    def normalize_session_key(self, session_key: str) -> str:
        return _safe_component(session_key or "main")


class R1NativeGatewayAdapter(R1HermesAdapter):
    """Prototype native Hermes Gateway adapter for Rabbit R1.

    The WebSocket authentication, rate limiting, and chat event framing are inherited from
    the standalone adapter. The difference is that authenticated chat text is converted to a
    gateway-style MessageEvent and sent to a supplied gateway message handler.
    """

    def __init__(
        self,
        config: R1HermesConfig,
        *,
        gateway_message_handler: GatewayMessageHandler,
        platform_name: str = DEFAULT_NATIVE_PLATFORM,
        platform_toolsets: Sequence[str] = DEFAULT_NATIVE_TOOLSETS,
        allow_high_impact_toolsets: bool = False,
        allowed_device_ids: Iterable[str] | None = None,
    ):
        self.bridge = R1GatewayMessageBridge(
            gateway_message_handler=gateway_message_handler,
            platform_name=platform_name,
            platform_toolsets=platform_toolsets,
            allow_high_impact_toolsets=allow_high_impact_toolsets,
        )
        self.allowed_device_ids = (
            frozenset(_safe_component(device_id) for device_id in allowed_device_ids)
            if allowed_device_ids is not None
            else None
        )
        self._active_sockets: dict[tuple[str, str], web.WebSocketResponse] = {}
        self._active_socket_lock = asyncio.Lock()
        super().__init__(config, message_handler=self.bridge)

    def _authenticate_connect(
        self, supplied: str, *, device_id: str, display_name: str
    ) -> tuple[bool, str, str]:
        safe_device_id = _safe_component(device_id)
        if self.allowed_device_ids is not None and safe_device_id not in self.allowed_device_ids:
            return False, "device is not allowed", ""
        return super()._authenticate_connect(
            supplied,
            device_id=safe_device_id,
            display_name=display_name,
        )

    async def _on_chat_session_active(
        self, ws: web.WebSocketResponse, *, device_id: str, session_key: str
    ) -> None:
        safe_session_key = self.bridge.normalize_session_key(session_key)
        async with self._active_socket_lock:
            self._active_sockets[(_safe_component(device_id), safe_session_key)] = ws

    async def _on_ws_closed(self, ws: web.WebSocketResponse, *, device_id: str) -> None:
        safe_device_id = _safe_component(device_id)
        async with self._active_socket_lock:
            stale_keys = [
                key
                for key, socket in self._active_sockets.items()
                if socket is ws and key[0] == safe_device_id
            ]
            for key in stale_keys:
                self._active_sockets.pop(key, None)

    async def send_text(
        self,
        *,
        device_id: str,
        session_key: str,
        text: str,
        run_id: str | None = None,
    ) -> bool:
        """Send a gateway reply/proactive text event to an active R1 WebSocket.

        This intentionally no-ops when no authenticated socket has activated the session. The
        prototype does not queue messages or persist delivery state.
        """

        key = (_safe_component(device_id), self.bridge.normalize_session_key(session_key or "main"))
        async with self._active_socket_lock:
            ws = self._active_sockets.get(key)
            if ws is None or ws.closed:
                self._active_sockets.pop(key, None)
                return False

        try:
            await ws.send_json(_chat_final_event(run_id or secrets.token_hex(8), key[1], text))
        except (ConnectionResetError, RuntimeError):
            async with self._active_socket_lock:
                if self._active_sockets.get(key) is ws:
                    self._active_sockets.pop(key, None)
            return False
        return True


def _reply_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(result, "content", None)
    extracted = _content_to_text(content)
    if extracted is not None:
        return extracted
    if isinstance(result, Mapping):
        extracted = _content_to_text(result.get("content")) or _clean_text(result.get("text"))
        if extracted is not None:
            return extracted
    return "Gateway returned an unsupported response."


def _content_to_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, Sequence) or isinstance(content, (bytes, bytearray)):
        return None
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping) and item.get("type") in {"text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    text = "".join(parts).strip()
    return text or None


def _safe_component(value: str) -> str:
    text = str(value).strip()
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in text)
    safe = safe.strip("-")[:120]
    return safe or "unknown"


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _chat_final_event(run_id: str, session_key: str, text: str) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "chat",
        "payload": {
            "runId": run_id,
            "sessionKey": session_key,
            "seq": 1,
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": str(text or "")}],
                "timestamp": int(time.time() * 1000),
            },
        },
    }
