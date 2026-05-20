from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .payloads import (
    PayloadParseError,
    parse_chat_send_params,
    parse_connect_params,
    request_params,
)

DEFAULT_PORT = 18789
DEFAULT_HOST = "127.0.0.1"
STATE_FILE = "devices.json"
TOKEN_BYTES = 32


@dataclass(frozen=True)
class R1HermesConfig:
    gateway_token: str
    state_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_message_chars: int = 4_000
    per_device_concurrency: int = 1
    rate_limit_messages: int = 12
    rate_limit_window_seconds: int = 60
    unauthenticated_timeout_seconds: int = 30

    @classmethod
    def from_env(cls, *, state_dir: Path) -> "R1HermesConfig":
        token = os.environ.get("R1_HERMES_GATEWAY_TOKEN", "")
        if not token:
            raise ValueError("R1_HERMES_GATEWAY_TOKEN is required")
        return cls(
            gateway_token=token,
            state_dir=state_dir,
            host=os.environ.get("R1_HERMES_HOST", DEFAULT_HOST),
            port=int(os.environ.get("R1_HERMES_PORT", str(DEFAULT_PORT))),
            max_message_chars=int(os.environ.get("R1_HERMES_MAX_MESSAGE_CHARS", "4000")),
            per_device_concurrency=int(os.environ.get("R1_HERMES_PER_DEVICE_CONCURRENCY", "1")),
            rate_limit_messages=int(os.environ.get("R1_HERMES_RATE_LIMIT_MESSAGES", "12")),
            rate_limit_window_seconds=int(
                os.environ.get("R1_HERMES_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
        )


@dataclass
class DeviceRecord:
    device_id: str
    token_hash: str
    display_name: str = "Rabbit R1"
    created_at_ms: int = 0
    last_seen_at_ms: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "token_hash": self.token_hash,
            "display_name": self.display_name,
            "created_at_ms": self.created_at_ms,
            "last_seen_at_ms": self.last_seen_at_ms,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DeviceRecord":
        return cls(
            device_id=str(data["device_id"]),
            token_hash=str(data["token_hash"]),
            display_name=str(data.get("display_name") or "Rabbit R1"),
            created_at_ms=int(data.get("created_at_ms") or 0),
            last_seen_at_ms=int(data.get("last_seen_at_ms") or 0),
        )


class DeviceState:
    """Device-token store that persists only hashes and binds tokens to device IDs."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, stat.S_IRWXU)
        self.path = self.state_dir / STATE_FILE
        self.devices: dict[str, DeviceRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.devices = {
            device_id: DeviceRecord.from_json(record)
            for device_id, record in data.get("devices", {}).items()
        }

    def save(self) -> None:
        payload = {"devices": {k: v.to_json() for k, v in sorted(self.devices.items())}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def issue_device_token(self, device_id: str, *, display_name: str = "Rabbit R1") -> str:
        now = _now_ms()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.devices[device_id] = DeviceRecord(
            device_id=device_id,
            token_hash=_hash_token(token),
            display_name=display_name or "Rabbit R1",
            created_at_ms=now,
            last_seen_at_ms=now,
        )
        self.save()
        return token

    def verify_device_token(self, device_id: str, token: str) -> bool:
        record = self.devices.get(device_id)
        if not record:
            return False
        ok = hmac.compare_digest(record.token_hash, _hash_token(token))
        if ok:
            record.last_seen_at_ms = _now_ms()
            self.save()
        return ok

    def revoke(self, device_id: str) -> bool:
        existed = device_id in self.devices
        self.devices.pop(device_id, None)
        self.save()
        return existed


class FixedWindowRateLimiter:
    def __init__(self, max_events: int, window_seconds: int):
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.events[key]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        return True


class R1HermesAdapter:
    """Hardened OpenClaw-compatible WebSocket adapter.

    message_handler receives: ``await handler(text, device_id=..., session_key=...)``
    and must return a text response.
    """

    def __init__(self, config: R1HermesConfig, *, message_handler):
        if not config.gateway_token:
            raise ValueError("gateway_token is required")
        self.config = config
        self.message_handler = message_handler
        self.state = DeviceState(config.state_dir)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._app: web.Application | None = None
        self._rate_limiter = FixedWindowRateLimiter(
            config.rate_limit_messages, config.rate_limit_window_seconds
        )
        self._inflight_by_device: dict[str, int] = defaultdict(int)
        self._inflight_lock = asyncio.Lock()

    async def start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/", self._ws_handler)
        self._app.router.add_get("/healthz", self._healthz)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        self._site = None
        self._runner = None
        self._app = None

    async def _healthz(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "paired": len(self.state.devices)})

    async def _ws_handler(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Upgrade", "").lower() != "websocket":
            raise web.HTTPNotFound()

        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=self.config.max_message_chars * 4)
        await ws.prepare(request)
        nonce = secrets.token_hex(16)
        authenticated = False
        device_id = ""
        session_started = time.monotonic()

        await ws.send_json(
            {
                "type": "event",
                "event": "connect.challenge",
                "payload": {"nonce": nonce, "ts": _now_ms()},
            }
        )

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            if (
                not authenticated
                and time.monotonic() - session_started > self.config.unauthenticated_timeout_seconds
            ):
                await _send_error(ws, None, "UNAUTHENTICATED", "connect timeout")
                await ws.close(code=1008, message=b"connect timeout")
                break
            try:
                frame = json.loads(msg.data)
            except json.JSONDecodeError:
                await _send_error(ws, None, "BAD_JSON", "invalid JSON")
                continue
            if not isinstance(frame, dict):
                await _send_error(ws, None, "BAD_REQUEST", "request frame must be an object")
                continue

            method = frame.get("method")
            rid = frame.get("id")
            if frame.get("type") != "req":
                await _send_error(ws, rid, "BAD_REQUEST", "expected req frame")
                continue
            if not isinstance(method, str) or not method.strip():
                await _send_error(ws, rid, "BAD_REQUEST", "method is required")
                continue

            if method == "connect":
                try:
                    connect_request = parse_connect_params(request_params(frame))
                except PayloadParseError as exc:
                    await _send_error(ws, rid, exc.code, exc.message)
                    continue

                ok, device_id_or_error, device_token = self._authenticate_connect(
                    connect_request.auth_token,
                    device_id=connect_request.device_id,
                    display_name=connect_request.display_name,
                )
                if not ok:
                    await _send_error(ws, rid, "UNAUTHORIZED", device_id_or_error)
                    await ws.close(code=1008, message=b"unauthorized")
                    break
                device_id = device_id_or_error
                authenticated = True
                await ws.send_json(_hello_response(rid, device_token))
                continue

            if not authenticated:
                await _send_error(ws, rid, "UNAUTHENTICATED", "connect required before requests")
                continue

            if method in {"health", "gateway.health"}:
                await ws.send_json({"type": "res", "id": rid, "ok": True, "payload": {"ok": True}})
            elif method in {"system-presence", "tools.catalog", "tools.effective"}:
                await ws.send_json({"type": "res", "id": rid, "ok": True, "payload": {}})
            elif method == "chat.history":
                try:
                    params = request_params(frame)
                except PayloadParseError as exc:
                    await _send_error(ws, rid, exc.code, exc.message)
                    continue
                await ws.send_json(
                    {
                        "type": "res",
                        "id": rid,
                        "ok": True,
                        "payload": {"sessionKey": params.get("sessionKey", "main"), "messages": []},
                    }
                )
            elif method == "chat.send":
                await self._handle_chat_send(ws, rid, frame, device_id)
            else:
                await _send_error(ws, rid, "UNKNOWN_METHOD", "unsupported method")

        return ws

    def _authenticate_connect(
        self, supplied: str, *, device_id: str, display_name: str
    ) -> tuple[bool, str, str]:
        if hmac.compare_digest(supplied, self.config.gateway_token):
            return (
                True,
                device_id,
                self.state.issue_device_token(device_id, display_name=display_name),
            )

        if self.state.verify_device_token(device_id, supplied):
            return True, device_id, supplied

        return False, "auth token mismatch", ""

    async def _handle_chat_send(
        self, ws: web.WebSocketResponse, rid: str | None, frame: dict[str, Any], device_id: str
    ) -> None:
        try:
            chat_request = parse_chat_send_params(request_params(frame))
        except PayloadParseError as exc:
            await _send_error(ws, rid, exc.code, exc.message)
            return
        message_text = chat_request.message
        if len(message_text) > self.config.max_message_chars:
            await _send_error(ws, rid, "MESSAGE_TOO_LARGE", "message exceeds limit")
            return
        if not self._rate_limiter.allow(device_id):
            await _send_error(ws, rid, "RATE_LIMITED", "too many messages")
            return

        async with self._inflight_lock:
            if self._inflight_by_device[device_id] >= self.config.per_device_concurrency:
                await _send_error(ws, rid, "BUSY", "device has too many in-flight runs")
                return
            self._inflight_by_device[device_id] += 1

        run_id = str(chat_request.idempotency_key or rid or secrets.token_hex(8))
        session_key = chat_request.session_key
        await ws.send_json(
            {
                "type": "res",
                "id": rid,
                "ok": True,
                "payload": {"runId": run_id, "status": "started"},
            }
        )

        try:
            try:
                response = await self.message_handler(
                    message_text, device_id=device_id, session_key=session_key
                )
            except Exception as exc:  # pragma: no cover - defensive boundary
                response = f"Error: {str(exc)[:300]}"
        finally:
            async with self._inflight_lock:
                self._inflight_by_device[device_id] -= 1
                if self._inflight_by_device[device_id] <= 0:
                    self._inflight_by_device.pop(device_id, None)

        await ws.send_json(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": run_id,
                    "sessionKey": session_key,
                    "seq": 1,
                    "state": "final",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": str(response or "")}],
                        "timestamp": _now_ms(),
                    },
                },
            }
        )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _send_error(ws: web.WebSocketResponse, rid: Any, code: str, message: str) -> None:
    await ws.send_json(
        {"type": "res", "id": rid, "ok": False, "error": {"code": code, "message": message}}
    )


def _hello_response(rid: Any, device_token: str) -> dict[str, Any]:
    return {
        "type": "res",
        "id": rid,
        "ok": True,
        "payload": {
            "type": "hello-ok",
            "protocol": 3,
            "policy": {"tickIntervalMs": 15000},
            "auth": {
                "deviceToken": device_token,
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
            },
            "presence": [],
            "health": {"ok": True, "status": "ok"},
            "stateVersion": 1,
            "uptimeMs": 0,
        },
    }
