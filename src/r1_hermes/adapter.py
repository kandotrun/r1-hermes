from __future__ import annotations

import asyncio
import binascii
import copy
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from aiohttp import WSMsgType, web

from .audit import audit_log, hash_identifier
from .chat_errors import ChatRunError, ChatRunTimeoutError
from .frame_shape import build_frame_shape_log_fields
from .media import (
    DEFAULT_MEDIA_MAX_BYTES,
    DEFAULT_MEDIA_TTL_SECONDS,
    MediaUploadError,
    MediaUploadStore,
    StoredMediaFile,
)
from .payloads import (
    ImageAttachment,
    PayloadParseError,
    parse_chat_history_params,
    parse_chat_send_params,
    parse_connect_params,
    request_params,
)

DEFAULT_PORT = 18789
DEFAULT_HOST = "127.0.0.1"
DEFAULT_GLOBAL_CONCURRENCY = 2
STATE_FILE = "devices.json"
STATE_DIGEST_KEY_FILE = "device-token-hmac.key"
TOKEN_BYTES = 32
DIGEST_PREFIX = "hmac-sha256:v1:"
DIGEST_KEY_BYTES = 32
CONNECT_METHODS = {"connect", "gateway.connect"}
GATEWAY_CONNECT_ACK_EVENTS = ("connect.ok", "node.pair.approved")
POLICY_VIOLATION_CLOSE_CODE = 1008
DEFAULT_UNAUTHENTICATED_CONNECTION_LIMIT = 8
DEFAULT_UNAUTHENTICATED_ATTEMPT_LIMIT = 8
DEFAULT_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS = 60
DEFAULT_UNAUTHENTICATED_COOLDOWN_SECONDS = 60
PUBLIC_BIND_ERROR = (
    "Refusing wildcard bind host {host!r}. Binding the bearer-token gateway to all network "
    "interfaces can expose Rabbit R1 access to unintended clients. Use --host 127.0.0.1 with "
    "Tailscale Serve, a reverse proxy with mTLS or IP allowlisting, or a specific Tailscale/LAN "
    "IP. If this network boundary has been explicitly reviewed, set --allow-public-bind or "
    "R1_HERMES_ALLOW_PUBLIC_BIND=1."
)
DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS = 90 * 24 * 60 * 60
DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS = 30 * 24 * 60 * 60
DEFAULT_IDEMPOTENCY_CACHE_MAX_ENTRIES = 256
DEFAULT_IDEMPOTENCY_CACHE_TTL_SECONDS = 5 * 60
DEFAULT_CHAT_RUN_TIMEOUT_SECONDS = 180.0
DEFAULT_CHAT_HEARTBEAT_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class R1HermesConfig:
    gateway_token: str
    state_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_public_bind: bool = False
    max_message_chars: int = 4_000
    per_device_concurrency: int = 1
    global_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY
    rate_limit_messages: int = 12
    rate_limit_window_seconds: int = 60
    unauthenticated_timeout_seconds: int = 30
    unauthenticated_connection_limit: int = DEFAULT_UNAUTHENTICATED_CONNECTION_LIMIT
    unauthenticated_attempt_limit: int = DEFAULT_UNAUTHENTICATED_ATTEMPT_LIMIT
    unauthenticated_attempt_window_seconds: int = DEFAULT_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS
    unauthenticated_cooldown_seconds: int = DEFAULT_UNAUTHENTICATED_COOLDOWN_SECONDS
    device_token_max_age_seconds: int = DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS
    device_token_idle_timeout_seconds: int = DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS
    idempotency_cache_max_entries: int = DEFAULT_IDEMPOTENCY_CACHE_MAX_ENTRIES
    idempotency_cache_ttl_seconds: int = DEFAULT_IDEMPOTENCY_CACHE_TTL_SECONDS
    chat_run_timeout_seconds: float = DEFAULT_CHAT_RUN_TIMEOUT_SECONDS
    chat_heartbeat_interval_seconds: float = DEFAULT_CHAT_HEARTBEAT_INTERVAL_SECONDS
    media_max_file_bytes: int = DEFAULT_MEDIA_MAX_BYTES
    media_ttl_seconds: int = DEFAULT_MEDIA_TTL_SECONDS
    allow_remote_health: bool = False
    health_diagnostics: bool = False
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    allowed_device_ids: frozenset[str] | tuple[str, ...] | list[str] | None = None
    frame_shape_logging: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_device_ids",
            _normalize_allowed_device_ids(self.allowed_device_ids),
        )
        if not self.allow_public_bind and _is_wildcard_public_bind(self.host):
            raise ValueError(PUBLIC_BIND_ERROR.format(host=self.host))
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise ValueError("--tls-cert-file and --tls-key-file must be provided together")

    @classmethod
    def from_env(
        cls,
        *,
        state_dir: Path,
        host: str | None = None,
        port: int | None = None,
        allow_public_bind: bool | None = None,
        per_device_concurrency: int | None = None,
        global_concurrency: int | None = None,
        device_token_max_age_seconds: int | None = None,
        device_token_idle_timeout_seconds: int | None = None,
        idempotency_cache_max_entries: int | None = None,
        idempotency_cache_ttl_seconds: int | None = None,
        chat_run_timeout_seconds: float | None = None,
        chat_heartbeat_interval_seconds: float | None = None,
        media_max_file_bytes: int | None = None,
        media_ttl_seconds: int | None = None,
        allow_remote_health: bool | None = None,
        health_diagnostics: bool | None = None,
        tls_cert_file: Path | None = None,
        tls_key_file: Path | None = None,
        allowed_device_ids: list[str] | tuple[str, ...] | frozenset[str] | None = None,
        frame_shape_logging: bool | None = None,
    ) -> "R1HermesConfig":
        token = os.environ.get("R1_HERMES_GATEWAY_TOKEN", "")
        if not token:
            raise ValueError("R1_HERMES_GATEWAY_TOKEN is required")
        return cls(
            gateway_token=token,
            state_dir=state_dir,
            host=host if host is not None else os.environ.get("R1_HERMES_HOST", DEFAULT_HOST),
            port=(
                port
                if port is not None
                else int(os.environ.get("R1_HERMES_PORT", str(DEFAULT_PORT)))
            ),
            allow_public_bind=(
                _env_flag("R1_HERMES_ALLOW_PUBLIC_BIND")
                if allow_public_bind is None
                else allow_public_bind
            ),
            max_message_chars=int(os.environ.get("R1_HERMES_MAX_MESSAGE_CHARS", "4000")),
            per_device_concurrency=(
                per_device_concurrency
                if per_device_concurrency is not None
                else int(os.environ.get("R1_HERMES_PER_DEVICE_CONCURRENCY", "1"))
            ),
            global_concurrency=(
                global_concurrency
                if global_concurrency is not None
                else int(
                    os.environ.get("R1_HERMES_GLOBAL_CONCURRENCY", str(DEFAULT_GLOBAL_CONCURRENCY))
                )
            ),
            rate_limit_messages=int(os.environ.get("R1_HERMES_RATE_LIMIT_MESSAGES", "12")),
            rate_limit_window_seconds=int(
                os.environ.get("R1_HERMES_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
            unauthenticated_timeout_seconds=int(
                os.environ.get("R1_HERMES_UNAUTHENTICATED_TIMEOUT_SECONDS", "30")
            ),
            unauthenticated_connection_limit=int(
                os.environ.get(
                    "R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT",
                    str(DEFAULT_UNAUTHENTICATED_CONNECTION_LIMIT),
                )
            ),
            unauthenticated_attempt_limit=int(
                os.environ.get(
                    "R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT",
                    str(DEFAULT_UNAUTHENTICATED_ATTEMPT_LIMIT),
                )
            ),
            unauthenticated_attempt_window_seconds=int(
                os.environ.get(
                    "R1_HERMES_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS",
                    str(DEFAULT_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS),
                )
            ),
            unauthenticated_cooldown_seconds=int(
                os.environ.get(
                    "R1_HERMES_UNAUTHENTICATED_COOLDOWN_SECONDS",
                    str(DEFAULT_UNAUTHENTICATED_COOLDOWN_SECONDS),
                )
            ),
            device_token_max_age_seconds=int(
                device_token_max_age_seconds
                if device_token_max_age_seconds is not None
                else os.environ.get(
                    "R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS",
                    str(DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS),
                )
            ),
            device_token_idle_timeout_seconds=int(
                device_token_idle_timeout_seconds
                if device_token_idle_timeout_seconds is not None
                else os.environ.get(
                    "R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS",
                    str(DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS),
                )
            ),
            idempotency_cache_max_entries=int(
                idempotency_cache_max_entries
                if idempotency_cache_max_entries is not None
                else os.environ.get(
                    "R1_HERMES_IDEMPOTENCY_CACHE_MAX_ENTRIES",
                    str(DEFAULT_IDEMPOTENCY_CACHE_MAX_ENTRIES),
                )
            ),
            idempotency_cache_ttl_seconds=int(
                idempotency_cache_ttl_seconds
                if idempotency_cache_ttl_seconds is not None
                else os.environ.get(
                    "R1_HERMES_IDEMPOTENCY_CACHE_TTL_SECONDS",
                    str(DEFAULT_IDEMPOTENCY_CACHE_TTL_SECONDS),
                )
            ),
            chat_run_timeout_seconds=float(
                chat_run_timeout_seconds
                if chat_run_timeout_seconds is not None
                else os.environ.get(
                    "R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS",
                    os.environ.get("R1_HERMES_TIMEOUT", str(DEFAULT_CHAT_RUN_TIMEOUT_SECONDS)),
                )
            ),
            chat_heartbeat_interval_seconds=float(
                chat_heartbeat_interval_seconds
                if chat_heartbeat_interval_seconds is not None
                else os.environ.get(
                    "R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS",
                    str(DEFAULT_CHAT_HEARTBEAT_INTERVAL_SECONDS),
                )
            ),
            media_max_file_bytes=int(
                media_max_file_bytes
                if media_max_file_bytes is not None
                else os.environ.get("R1_HERMES_MEDIA_MAX_FILE_BYTES", str(DEFAULT_MEDIA_MAX_BYTES))
            ),
            media_ttl_seconds=int(
                media_ttl_seconds
                if media_ttl_seconds is not None
                else os.environ.get("R1_HERMES_MEDIA_TTL_SECONDS", str(DEFAULT_MEDIA_TTL_SECONDS))
            ),
            allow_remote_health=(
                _env_flag("R1_HERMES_ALLOW_REMOTE_HEALTH")
                if allow_remote_health is None
                else allow_remote_health
            ),
            health_diagnostics=(
                _env_flag("R1_HERMES_HEALTH_DIAGNOSTICS")
                if health_diagnostics is None
                else health_diagnostics
            ),
            tls_cert_file=(
                tls_cert_file
                if tls_cert_file is not None
                else _optional_env_path("R1_HERMES_TLS_CERT_FILE")
            ),
            tls_key_file=(
                tls_key_file
                if tls_key_file is not None
                else _optional_env_path("R1_HERMES_TLS_KEY_FILE")
            ),
            allowed_device_ids=(
                _allowed_device_ids_from_env()
                if allowed_device_ids is None
                else allowed_device_ids
            ),
            frame_shape_logging=(
                _env_flag("R1_HERMES_FRAME_SHAPE_LOGGING")
                if frame_shape_logging is None
                else frame_shape_logging
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
    def from_json(cls, data: dict[str, Any], *, missing_timestamp_ms: int = 0) -> "DeviceRecord":
        created_at_ms = _timestamp_from_json(
            data,
            "created_at_ms",
            default=missing_timestamp_ms,
        )
        last_seen_at_ms = _timestamp_from_json(
            data,
            "last_seen_at_ms",
            default=created_at_ms,
        )
        return cls(
            device_id=str(data["device_id"]),
            token_hash=str(data["token_hash"]),
            display_name=str(data.get("display_name") or "Rabbit R1"),
            created_at_ms=created_at_ms,
            last_seen_at_ms=last_seen_at_ms,
        )


@dataclass(frozen=True)
class DeviceTokenVerification:
    ok: bool
    rotated_digest: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    device_id: str = ""
    device_token: str = ""
    auth_type: str = ""
    device_token_rotated: bool = False
    failure_reason: str = ""


class DeviceState:
    """Device-token store that persists keyed digests and binds tokens to device IDs."""

    def __init__(
        self,
        state_dir: Path,
        *,
        device_token_max_age_seconds: int = DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS,
        device_token_idle_timeout_seconds: int = DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS,
    ):
        self.state_dir = state_dir
        self.device_token_max_age_seconds = max(0, int(device_token_max_age_seconds))
        self.device_token_idle_timeout_seconds = max(0, int(device_token_idle_timeout_seconds))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, stat.S_IRWXU)
        self.path = self.state_dir / STATE_FILE
        self.key_path = self.state_dir / STATE_DIGEST_KEY_FILE
        self._digest_key = self._load_or_create_digest_key()
        self.devices: dict[str, DeviceRecord] = {}
        self.load()

    def _load_or_create_digest_key(self) -> bytes:
        if self.key_path.is_symlink():
            raise ValueError("device token digest key path must not be a symlink")
        if self.key_path.exists():
            os.chmod(self.key_path, stat.S_IRUSR | stat.S_IWUSR)
            raw = self.key_path.read_text().strip()
            try:
                key = binascii.unhexlify(raw.encode("ascii"))
            except (binascii.Error, ValueError) as exc:
                raise ValueError("device token digest key is not valid hex") from exc
            if len(key) < DIGEST_KEY_BYTES:
                raise ValueError("device token digest key is too short")
            return key

        key = secrets.token_bytes(DIGEST_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(self.key_path, flags, stat.S_IRUSR | stat.S_IWUSR)
        try:
            with os.fdopen(fd, "w") as handle:
                fd = -1
                handle.write(f"{key.hex()}\n")
        finally:
            if fd != -1:  # pragma: no cover - defensive cleanup
                os.close(fd)
        os.chmod(self.key_path, stat.S_IRUSR | stat.S_IWUSR)
        return key

    def load(self) -> None:
        if not self.path.exists():
            self.devices = {}
            return
        data = json.loads(self.path.read_text())
        loaded_at_ms = _now_ms()
        migrated_timestamps = False
        devices = {}
        for device_id, record in data.get("devices", {}).items():
            if not _has_valid_timestamp(record, "created_at_ms") or not _has_valid_timestamp(
                record,
                "last_seen_at_ms",
            ):
                migrated_timestamps = True
            devices[device_id] = DeviceRecord.from_json(
                record,
                missing_timestamp_ms=loaded_at_ms,
            )
        self.devices = devices
        if migrated_timestamps:
            self.save()

    def save(self) -> None:
        payload = {"devices": {k: v.to_json() for k, v in sorted(self.devices.items())}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def issue_device_token(self, device_id: str, *, display_name: str = "Rabbit R1") -> str:
        self.load()
        now = _now_ms()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.devices[device_id] = DeviceRecord(
            device_id=device_id,
            token_hash=_hash_token(token, self._digest_key),
            display_name=display_name or "Rabbit R1",
            created_at_ms=now,
            last_seen_at_ms=now,
        )
        self.save()
        return token

    def verify_device_token(self, device_id: str, token: str) -> bool:
        return self.verify_device_token_detailed(device_id, token).ok

    def verify_device_token_detailed(self, device_id: str, token: str) -> DeviceTokenVerification:
        self.load()
        record = self.devices.get(device_id)
        if not record:
            return DeviceTokenVerification(False, reason="unknown_device")
        now = _now_ms()
        token_hash = _hash_token(token, self._digest_key)
        ok = hmac.compare_digest(record.token_hash, token_hash)
        needs_upgrade = False
        if not ok and _is_legacy_token_hash(record.token_hash):
            ok = hmac.compare_digest(record.token_hash, _legacy_hash_token(token))
            needs_upgrade = ok
        if not ok:
            return DeviceTokenVerification(False, reason="token_mismatch")
        if self.is_expired(record, now_ms=now):
            return DeviceTokenVerification(False, reason="device_token_expired")
        if needs_upgrade:
            record.token_hash = token_hash
        record.last_seen_at_ms = now
        self.save()
        return DeviceTokenVerification(True, rotated_digest=needs_upgrade)

    def device_ids(self) -> list[str]:
        self.load()
        return sorted(self.devices)

    def revoke(self, device_id: str) -> bool:
        self.load()
        existed = device_id in self.devices
        self.devices.pop(device_id, None)
        if existed:
            self.save()
        audit_log(
            "info",
            "device.revoke",
            device_id_hash=hash_identifier(device_id),
            removed=existed,
        )
        return existed

    def revoke_all(self) -> list[str]:
        self.load()
        revoked = sorted(self.devices)
        if not revoked:
            audit_log("info", "device.revoke_all", revoked=0, device_id_hashes=[])
            return []
        self.devices.clear()
        self.save()
        audit_log(
            "info",
            "device.revoke_all",
            revoked=len(revoked),
            device_id_hashes=[hash_identifier(device_id) for device_id in revoked],
        )
        return revoked

    def prune_expired(self) -> int:
        self.load()
        now = _now_ms()
        expired = [
            device_id
            for device_id, record in self.devices.items()
            if self.is_expired(record, now_ms=now)
        ]
        for device_id in expired:
            self.devices.pop(device_id, None)
        if expired:
            self.save()
        audit_log("info", "device.cleanup", removed=len(expired))
        return len(expired)

    def is_expired(self, record: DeviceRecord, *, now_ms: int | None = None) -> bool:
        now_ms = _now_ms() if now_ms is None else now_ms
        if _is_ttl_expired(
            record.created_at_ms,
            now_ms=now_ms,
            ttl_seconds=self.device_token_max_age_seconds,
        ):
            return True
        return _is_ttl_expired(
            record.last_seen_at_ms,
            now_ms=now_ms,
            ttl_seconds=self.device_token_idle_timeout_seconds,
        )


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


class UnauthenticatedPeerLimiter:
    def __init__(
        self,
        *,
        max_connections: int,
        max_attempts: int,
        window_seconds: int,
        cooldown_seconds: int,
    ):
        self.max_connections = max_connections
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.active_connections: dict[str, int] = {}
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.cooldowns: dict[str, float] = {}

    def allow_connection(self, key: str) -> bool:
        self._prune(key)
        if self._is_cooled_down(key):
            return False
        active = self.active_connections.get(key, 0)
        if self.max_connections > 0 and active >= self.max_connections:
            return False
        self.active_connections[key] = active + 1
        return True

    def release_connection(self, key: str) -> None:
        active = self.active_connections.get(key)
        if active is None:
            return
        if active <= 1:
            self.active_connections.pop(key, None)
            return
        self.active_connections[key] = active - 1

    def record_attempt(self, key: str) -> bool:
        now = time.monotonic()
        self._prune(key, now=now)
        if self._is_cooled_down(key, now=now):
            return False

        q = self.attempts[key]
        if self.max_attempts > 0 and len(q) >= self.max_attempts:
            self.cooldowns[key] = now + self.cooldown_seconds
            return False

        q.append(now)
        if self.max_attempts > 0 and len(q) >= self.max_attempts:
            self.cooldowns[key] = now + self.cooldown_seconds
        return True

    def clear(self, key: str) -> None:
        self.attempts.pop(key, None)
        self.cooldowns.pop(key, None)

    def _prune(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self.attempts.get(key)
        if q is not None:
            while q and now - q[0] > self.window_seconds:
                q.popleft()
            if not q:
                self.attempts.pop(key, None)

        until = self.cooldowns.get(key)
        if until is not None and now >= until:
            self.cooldowns.pop(key, None)

    def _is_cooled_down(self, key: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        until = self.cooldowns.get(key)
        return until is not None and now < until


@dataclass
class IdempotencyCacheEntry:
    run_id: str
    session_key: str
    state: str
    updated_at: float
    final_event: dict[str, Any] | None = None


class IdempotencyCache:
    """Bounded in-memory chat.send idempotency cache scoped by device/session."""

    def __init__(self, *, max_entries: int, ttl_seconds: int):
        self.max_entries = max(0, int(max_entries))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._entries: OrderedDict[tuple[str, str, str], IdempotencyCacheEntry] = OrderedDict()

    def get(
        self,
        *,
        device_id: str,
        session_key: str,
        idempotency_key: str,
    ) -> IdempotencyCacheEntry | None:
        now = time.monotonic()
        self._prune(now=now)
        key = self._key(device_id, session_key, idempotency_key)
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.updated_at = now
        self._entries.move_to_end(key)
        return entry

    def reserve(
        self,
        *,
        device_id: str,
        session_key: str,
        idempotency_key: str,
        run_id: str,
    ) -> None:
        if self.max_entries <= 0:
            return
        now = time.monotonic()
        self._prune(now=now)
        self._entries[self._key(device_id, session_key, idempotency_key)] = IdempotencyCacheEntry(
            run_id=run_id,
            session_key=session_key,
            state="inflight",
            updated_at=now,
        )
        self._evict_over_limit()

    def complete(
        self,
        *,
        device_id: str,
        session_key: str,
        idempotency_key: str,
        run_id: str,
        final_event: dict[str, Any],
    ) -> None:
        key = self._key(device_id, session_key, idempotency_key)
        entry = self._entries.get(key)
        if entry is None or entry.run_id != run_id:
            return
        entry.state = "completed"
        entry.updated_at = time.monotonic()
        entry.final_event = copy.deepcopy(final_event)
        self._entries.move_to_end(key)
        self._evict_over_limit()

    def discard(
        self,
        *,
        device_id: str,
        session_key: str,
        idempotency_key: str,
        run_id: str,
    ) -> None:
        key = self._key(device_id, session_key, idempotency_key)
        entry = self._entries.get(key)
        if entry is not None and entry.run_id == run_id:
            self._entries.pop(key, None)

    def _prune(self, *, now: float) -> None:
        expired = [
            key for key, entry in self._entries.items() if now - entry.updated_at > self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _evict_over_limit(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        self._prune(now=time.monotonic())
        return len(self._entries)

    @property
    def enabled(self) -> bool:
        return self.max_entries > 0

    @staticmethod
    def _key(device_id: str, session_key: str, idempotency_key: str) -> tuple[str, str, str]:
        return device_id, session_key, idempotency_key


class R1HermesAdapter:
    """Hardened OpenClaw-compatible WebSocket adapter.

    message_handler receives: ``await handler(text, device_id=..., session_key=...)``
    and must return a text response.
    """

    def __init__(self, config: R1HermesConfig, *, message_handler):
        if not config.gateway_token:
            raise ValueError("gateway_token is required")
        self.config = config
        _validate_config(config)
        self.message_handler = message_handler
        self.state = DeviceState(
            config.state_dir,
            device_token_max_age_seconds=config.device_token_max_age_seconds,
            device_token_idle_timeout_seconds=config.device_token_idle_timeout_seconds,
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._app: web.Application | None = None
        self._rate_limiter = FixedWindowRateLimiter(
            config.rate_limit_messages, config.rate_limit_window_seconds
        )
        self._unauthenticated_limiter = UnauthenticatedPeerLimiter(
            max_connections=config.unauthenticated_connection_limit,
            max_attempts=config.unauthenticated_attempt_limit,
            window_seconds=config.unauthenticated_attempt_window_seconds,
            cooldown_seconds=config.unauthenticated_cooldown_seconds,
        )
        self._inflight_by_device: dict[str, int] = defaultdict(int)
        self._global_inflight = 0
        self._inflight_lock = asyncio.Lock()
        self._idempotency_cache = IdempotencyCache(
            max_entries=config.idempotency_cache_max_entries,
            ttl_seconds=config.idempotency_cache_ttl_seconds,
        )
        self.media_store = MediaUploadStore(
            config.state_dir,
            max_file_bytes=config.media_max_file_bytes,
            ttl_seconds=config.media_ttl_seconds,
        )

    async def start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/", self._ws_handler)
        self._app.router.add_get("/healthz", self._healthz)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            self.config.host,
            self.config.port,
            ssl_context=_server_ssl_context(self.config),
        )
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        self._site = None
        self._runner = None
        self._app = None

    async def _healthz(self, request: web.Request) -> web.Response:
        if not self.config.allow_remote_health and not _is_local_request(request):
            raise web.HTTPForbidden(text="health check is local-only")
        payload = {"ok": True}
        if self.config.health_diagnostics:
            payload["paired"] = len(self.state.devices)
        return web.json_response(payload)

    async def _ws_handler(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Upgrade", "").lower() != "websocket":
            raise web.HTTPNotFound()

        peer_key = _peer_key(request)
        ws = web.WebSocketResponse(
            heartbeat=20,
            max_msg_size=_websocket_max_message_size(self.config),
        )
        await ws.prepare(request)
        if not self._unauthenticated_limiter.allow_connection(peer_key):
            audit_log(
                "warning",
                "rate_limited",
                phase="unauthenticated_connection",
                peer_hash=hash_identifier(peer_key),
                limit=self.config.unauthenticated_connection_limit,
            )
            await _send_error(ws, None, "RATE_LIMITED", "too many unauthenticated attempts")
            await ws.close(code=POLICY_VIOLATION_CLOSE_CODE, message=b"rate limited")
            return ws

        nonce = secrets.token_hex(16)
        authenticated = False
        device_id = ""
        session_started = time.monotonic()
        active_chat_tasks: dict[asyncio.Task[None], bool] = {}

        await ws.send_json(
            {
                "type": "event",
                "event": "connect.challenge",
                "payload": {"nonce": nonce, "ts": _now_ms()},
            }
        )
        audit_log("info", "connect.challenge_issued", peer_hash=hash_identifier(peer_key))

        try:
            async for msg in ws:
                next_device_id, authenticated = await self._handle_ws_message(
                    ws,
                    msg,
                    authenticated=authenticated,
                    device_id=device_id,
                    session_started=session_started,
                    peer_key=peer_key,
                    active_chat_tasks=active_chat_tasks,
                )
                device_id = next_device_id or device_id
                if ws.closed:
                    break
        finally:
            await _cancel_active_chat_tasks(active_chat_tasks)
            if not authenticated:
                self._unauthenticated_limiter.release_connection(peer_key)
            if authenticated and device_id:
                await self._on_ws_closed(ws, device_id=device_id)

        return ws

    async def _handle_ws_message(
        self,
        ws: web.WebSocketResponse,
        msg: Any,
        *,
        authenticated: bool,
        device_id: str,
        session_started: float,
        peer_key: str,
        active_chat_tasks: dict[asyncio.Task[None], bool],
    ) -> tuple[str, bool]:
        if msg.type != WSMsgType.TEXT:
            return device_id, authenticated
        if (
            not authenticated
            and time.monotonic() - session_started > self.config.unauthenticated_timeout_seconds
        ):
            audit_log("warning", "auth.timeout", peer_hash=hash_identifier(peer_key))
            await _send_error(ws, None, "UNAUTHENTICATED", "connect timeout")
            await ws.close(code=POLICY_VIOLATION_CLOSE_CODE, message=b"connect timeout")
            return device_id, authenticated

        if not authenticated and not self._unauthenticated_limiter.record_attempt(peer_key):
            audit_log(
                "warning",
                "rate_limited",
                phase="unauthenticated",
                peer_hash=hash_identifier(peer_key),
                limit=self.config.unauthenticated_attempt_limit,
                window_seconds=self.config.unauthenticated_attempt_window_seconds,
            )
            await _send_error(ws, None, "RATE_LIMITED", "too many unauthenticated attempts")
            await ws.close(code=POLICY_VIOLATION_CLOSE_CODE, message=b"rate limited")
            return device_id, authenticated

        try:
            frame = json.loads(msg.data)
        except json.JSONDecodeError:
            audit_log("warning", "auth.parser_error", error_code="BAD_JSON")
            await _send_error(ws, None, "BAD_JSON", "invalid JSON")
            return device_id, authenticated
        if not isinstance(frame, dict):
            audit_log("warning", "auth.parser_error", error_code="BAD_REQUEST")
            await _send_error(ws, None, "BAD_REQUEST", "request frame must be an object")
            return device_id, authenticated

        method = frame.get("method")
        rid = frame.get("id")
        self._log_frame_shape(
            frame,
            authenticated=authenticated,
            device_id=device_id if authenticated else "",
        )
        if frame.get("type") != "req":
            if not authenticated:
                audit_log("warning", "auth.parser_error", error_code="BAD_REQUEST")
            await _send_error(ws, rid, "BAD_REQUEST", "expected req frame")
            return device_id, authenticated
        if not isinstance(method, str) or not method.strip():
            if not authenticated:
                audit_log("warning", "auth.parser_error", error_code="BAD_REQUEST")
            await _send_error(ws, rid, "BAD_REQUEST", "method is required")
            return device_id, authenticated

        if method in CONNECT_METHODS:
            if authenticated:
                await _send_error(
                    ws,
                    rid,
                    "ALREADY_CONNECTED",
                    "connect must be done before authentication; reconnect required",
                )
                return device_id, authenticated
            next_device_id, next_authenticated = await self._handle_connect(ws, rid, frame)
            if next_authenticated:
                self._unauthenticated_limiter.release_connection(peer_key)
                self._unauthenticated_limiter.clear(peer_key)
            return next_device_id, next_authenticated
        if not authenticated:
            audit_log(
                "warning",
                "auth.failure",
                reason="request_before_connect",
                method_hash=hash_identifier(method),
            )
            await _send_error(ws, rid, "UNAUTHENTICATED", "connect required before requests")
            return device_id, authenticated
        await self._handle_authenticated_request(
            ws,
            rid,
            method,
            frame,
            device_id,
            active_chat_tasks=active_chat_tasks,
        )
        return device_id, authenticated

    async def _handle_connect(
        self, ws: web.WebSocketResponse, rid: Any, frame: dict[str, Any]
    ) -> tuple[str, bool]:
        try:
            connect_request = parse_connect_params(request_params(frame))
        except PayloadParseError as exc:
            audit_log(
                "warning",
                "auth.parser_error",
                error_code=exc.code,
                method=str(frame.get("method") or ""),
            )
            await _send_error(ws, rid, exc.code, exc.message)
            return "", False

        auth_result = self._authenticate_connect(
            connect_request.auth_token,
            device_id=connect_request.device_id,
            display_name=connect_request.display_name,
        )
        if not auth_result.ok:
            audit_log(
                "warning",
                "auth.failure",
                reason=auth_result.failure_reason or "token_mismatch",
                method=str(frame.get("method") or ""),
                device_id_hash=hash_identifier(connect_request.device_id),
            )
            await _send_error(ws, rid, "UNAUTHORIZED", "auth token mismatch")
            await ws.close(code=POLICY_VIOLATION_CLOSE_CODE, message=b"unauthorized")
            return "", False
        audit_log(
            "info",
            "auth.success",
            method=str(frame.get("method") or ""),
            auth_type=auth_result.auth_type,
            device_id_hash=hash_identifier(auth_result.device_id),
            device_token_rotated=auth_result.device_token_rotated,
        )
        await ws.send_json(_hello_response(rid, auth_result.device_token))
        if frame.get("method") == "gateway.connect":
            for event in GATEWAY_CONNECT_ACK_EVENTS:
                await ws.send_json(_connect_ack_event(event, device_id=auth_result.device_id))
        await self._on_ws_authenticated(ws, device_id=auth_result.device_id)
        return auth_result.device_id, True

    async def _handle_authenticated_request(
        self,
        ws: web.WebSocketResponse,
        rid: Any,
        method: str,
        frame: dict[str, Any],
        device_id: str,
        active_chat_tasks: dict[asyncio.Task[None], bool] | None = None,
    ) -> None:
        if method in {"health", "gateway.health"}:
            await ws.send_json(
                {"type": "res", "id": rid, "ok": True, "payload": {"ok": True}}
            )
        elif method in {"system-presence", "tools.catalog", "tools.effective"}:
            await ws.send_json({"type": "res", "id": rid, "ok": True, "payload": {}})
        elif method == "chat.history":
            await self._handle_chat_history(ws, rid, frame)
        elif method == "chat.send":
            if active_chat_tasks is None:
                await self._handle_chat_send(ws, rid, frame, device_id)
                return
            task = asyncio.create_task(
                self._handle_chat_send(ws, rid, frame, device_id),
                name="r1-hermes-chat-send",
            )
            active_chat_tasks[task] = _chat_task_cancel_on_disconnect(
                frame,
                idempotency_cache_enabled=self._idempotency_cache.enabled,
            )
            task.add_done_callback(lambda done: _discard_finished_task(active_chat_tasks, done))
        else:
            await _send_error(ws, rid, "UNKNOWN_METHOD", "unsupported method")

    def _log_frame_shape(
        self,
        frame: dict[str, Any],
        *,
        authenticated: bool,
        device_id: str = "",
    ) -> None:
        if not self.config.frame_shape_logging:
            return
        audit_log(
            "info",
            "frame.shape",
            **build_frame_shape_log_fields(
                frame,
                authenticated=authenticated,
                device_id_hash=hash_identifier(device_id) if authenticated and device_id else "",
            ),
        )

    async def _handle_chat_history(
        self, ws: web.WebSocketResponse, rid: Any, frame: dict[str, Any]
    ) -> None:
        try:
            history_request = parse_chat_history_params(request_params(frame))
        except PayloadParseError as exc:
            await _send_error(ws, rid, exc.code, exc.message)
            return
        await ws.send_json(
            {
                "type": "res",
                "id": rid,
                "ok": True,
                "payload": {
                    "sessionKey": history_request.session_key,
                    "messages": [],
                    "status": "unsupported",
                    "historySupported": False,
                    "storage": "none",
                },
            }
        )

    async def _on_ws_authenticated(self, ws: web.WebSocketResponse, *, device_id: str) -> None:
        del ws, device_id

    async def _on_ws_closed(self, ws: web.WebSocketResponse, *, device_id: str) -> None:
        del ws, device_id

    async def _on_chat_session_active(
        self, ws: web.WebSocketResponse, *, device_id: str, session_key: str
    ) -> None:
        del ws, device_id, session_key

    def _authenticate_connect(
        self, supplied: str, *, device_id: str, display_name: str
    ) -> AuthResult:
        if (
            self.config.allowed_device_ids is not None
            and device_id not in self.config.allowed_device_ids
        ):
            return AuthResult(ok=False, failure_reason="device_not_allowed")

        if hmac.compare_digest(supplied, self.config.gateway_token):
            return AuthResult(
                ok=True,
                device_id=device_id,
                device_token=self.state.issue_device_token(device_id, display_name=display_name),
                auth_type="gateway_token",
            )

        verification = self.state.verify_device_token_detailed(device_id, supplied)
        if verification.ok:
            return AuthResult(
                ok=True,
                device_id=device_id,
                device_token=supplied,
                auth_type="device_token",
                device_token_rotated=verification.rotated_digest,
            )

        reason = (
            "device_token_expired"
            if verification.reason == "device_token_expired"
            else "token_mismatch"
        )
        return AuthResult(ok=False, failure_reason=reason)

    async def _handle_chat_send(
        self, ws: web.WebSocketResponse, rid: str | None, frame: dict[str, Any], device_id: str
    ) -> None:
        try:
            chat_request = parse_chat_send_params(request_params(frame))
        except PayloadParseError as exc:
            audit_log(
                "warning",
                "chat.parser_error",
                error_code=exc.code,
                device_id_hash=hash_identifier(device_id),
            )
            await _send_error(ws, rid, exc.code, exc.message)
            return
        message_text = chat_request.message
        attachments = chat_request.attachments
        if len(message_text) > self.config.max_message_chars:
            audit_log(
                "warning",
                "chat.message_too_large",
                device_id_hash=hash_identifier(device_id),
                message_chars=len(message_text),
                max_message_chars=self.config.max_message_chars,
            )
            await _send_error(ws, rid, "MESSAGE_TOO_LARGE", "message exceeds limit")
            return
        stored_media: tuple[StoredMediaFile, ...] = ()
        try:
            stored_media = self.media_store.store_all(chat_request.attachments)
        except MediaUploadError as exc:
            audit_log(
                "warning",
                "chat.media_rejected",
                error_code=exc.code,
                device_id_hash=hash_identifier(device_id),
                attachments=len(chat_request.attachments),
            )
            await _send_error(ws, rid, exc.code, exc.message)
            return
        handler_text = _compose_hermes_prompt(message_text, stored_media)

        run_id = str(chat_request.idempotency_key or rid or secrets.token_hex(8))
        session_key = chat_request.session_key
        idempotency_key = chat_request.idempotency_key
        busy = False
        busy_reason = ""
        global_inflight = 0
        device_inflight = 0
        rate_limited = False
        duplicate_entry: IdempotencyCacheEntry | None = None
        async with self._inflight_lock:
            global_inflight = self._global_inflight
            device_inflight = self._inflight_by_device[device_id]
            if idempotency_key:
                duplicate_entry = self._idempotency_cache.get(
                    device_id=device_id,
                    session_key=session_key,
                    idempotency_key=idempotency_key,
                )
            if duplicate_entry is not None:
                pass
            elif not self._rate_limiter.allow(device_id):
                rate_limited = True
            elif self._global_inflight >= self.config.global_concurrency:
                busy = True
                busy_reason = "global_concurrency"
            elif self._inflight_by_device[device_id] >= self.config.per_device_concurrency:
                busy = True
                busy_reason = "per_device_concurrency"
            else:
                self._inflight_by_device[device_id] += 1
                self._global_inflight += 1
                if idempotency_key:
                    self._idempotency_cache.reserve(
                        device_id=device_id,
                        session_key=session_key,
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                    )
        if duplicate_entry is not None:
            self.media_store.remove(*(item.path for item in stored_media))
            await self._send_duplicate_chat_response(ws, rid, duplicate_entry, device_id=device_id)
            return
        if rate_limited:
            self.media_store.remove(*(item.path for item in stored_media))
            audit_log(
                "warning",
                "rate_limited",
                phase="authenticated_chat",
                device_id_hash=hash_identifier(device_id),
                limit=self.config.rate_limit_messages,
                window_seconds=self.config.rate_limit_window_seconds,
                message_chars=len(message_text),
            )
            await _send_error(ws, rid, "RATE_LIMITED", "too many messages")
            return
        if busy:
            self.media_store.remove(*(item.path for item in stored_media))
            audit_log(
                "warning",
                "busy_rejected",
                reason=busy_reason,
                device_id_hash=hash_identifier(device_id),
                global_inflight=global_inflight,
                global_limit=self.config.global_concurrency,
                device_inflight=device_inflight,
                per_device_limit=self.config.per_device_concurrency,
                message_chars=len(message_text),
            )
            await _send_error(ws, rid, "BUSY", "gateway is busy")
            return

        response = ""
        error: ChatRunError | None = None
        cancelled = False
        started_at = time.monotonic()
        seq_ref = {"value": 1}
        completed_event: dict[str, Any]
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await self._on_chat_session_active(ws, device_id=device_id, session_key=session_key)
            started_ack = {
                "type": "res",
                "id": rid,
                "ok": True,
                "payload": {"runId": run_id, "status": "started"},
            }
            if not await _send_json_if_open(ws, started_ack):
                self._record_chat_delivery_lost(
                    device_id=device_id,
                    session_key=session_key,
                    run_id=run_id,
                    started_at=started_at,
                    delivery_state="ack",
                )
                if idempotency_key:
                    self._idempotency_cache.discard(
                        device_id=device_id,
                        session_key=session_key,
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                    )
                return
            started_event = {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": run_id,
                    "sessionKey": session_key,
                    "seq": 1,
                    "state": "started",
                },
            }
            if not await _send_json_if_open(ws, started_event):
                self._record_chat_delivery_lost(
                    device_id=device_id,
                    session_key=session_key,
                    run_id=run_id,
                    started_at=started_at,
                    delivery_state="started",
                )
                if idempotency_key:
                    self._idempotency_cache.discard(
                        device_id=device_id,
                        session_key=session_key,
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                    )
                return
            audit_log(
                "info",
                "chat.run_started",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                run_id_hash=hash_identifier(run_id),
                message_chars=len(message_text),
                **_attachment_audit_fields(attachments),
                media_files=len(stored_media),
            )
            heartbeat_task = asyncio.create_task(
                _emit_chat_heartbeats(
                    ws,
                    run_id=run_id,
                    session_key=session_key,
                    interval_seconds=self.config.chat_heartbeat_interval_seconds,
                    next_seq=lambda: _next_chat_seq(seq_ref),
                    device_id=device_id,
                    started_at=started_at,
                ),
                name="r1-hermes-chat-heartbeat",
            )
            try:
                handler_kwargs: dict[str, Any] = {
                    "device_id": device_id,
                    "session_key": session_key,
                }
                if attachments:
                    handler_kwargs["attachments"] = attachments
                response = await asyncio.wait_for(
                    self.message_handler(handler_text, **handler_kwargs),
                    timeout=self.config.chat_run_timeout_seconds,
                )
            except asyncio.CancelledError:
                if idempotency_key:
                    self._idempotency_cache.discard(
                        device_id=device_id,
                        session_key=session_key,
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                    )
                cancelled = True
                raise
            except ChatRunError as exc:
                error = exc
            except (TimeoutError, asyncio.TimeoutError):
                error = ChatRunTimeoutError()
            except Exception:  # pragma: no cover - defensive boundary
                error = ChatRunError()
        finally:
            self.media_store.remove(*(item.path for item in stored_media))
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            async with self._inflight_lock:
                self._inflight_by_device[device_id] -= 1
                if self._inflight_by_device[device_id] <= 0:
                    self._inflight_by_device.pop(device_id, None)
                self._global_inflight -= 1
                if self._global_inflight < 0:  # pragma: no cover - defensive invariant guard
                    self._global_inflight = 0
            if cancelled:
                audit_log(
                    "info",
                    "chat.run_cancelled",
                    device_id_hash=hash_identifier(device_id),
                    session_key_hash=hash_identifier(session_key),
                    run_id_hash=hash_identifier(run_id),
                    reason=("websocket_disconnected" if _ws_closed(ws) else "handler_cancelled"),
                    duration_ms=_elapsed_ms(started_at),
                )

        if error is not None:
            completed_event = _chat_error_event(
                run_id, session_key, error, seq=_next_chat_seq(seq_ref)
            )
            audit_log(
                "error",
                "chat.run_error",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                run_id_hash=hash_identifier(run_id),
                error_code=error.code,
                safe_message=error.safe_message,
                **_attachment_audit_fields(attachments),
                duration_ms=_elapsed_ms(started_at),
            )
            if not await _send_json_if_open(ws, completed_event):
                self._record_chat_delivery_lost(
                    device_id=device_id,
                    session_key=session_key,
                    run_id=run_id,
                    started_at=started_at,
                    delivery_state="error",
                )
                if idempotency_key:
                    self._idempotency_cache.complete(
                        device_id=device_id,
                        session_key=session_key,
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                        final_event=completed_event,
                    )
                return
            if idempotency_key:
                self._idempotency_cache.complete(
                    device_id=device_id,
                    session_key=session_key,
                    idempotency_key=idempotency_key,
                    run_id=run_id,
                    final_event=completed_event,
                )
            return

        completed_event = _chat_final_event(
            run_id, session_key, response, seq=_next_chat_seq(seq_ref)
        )
        if not await _send_json_if_open(ws, completed_event):
            self._record_chat_delivery_lost(
                device_id=device_id,
                session_key=session_key,
                run_id=run_id,
                started_at=started_at,
                delivery_state="final",
            )
            if idempotency_key:
                self._idempotency_cache.complete(
                    device_id=device_id,
                    session_key=session_key,
                    idempotency_key=idempotency_key,
                    run_id=run_id,
                    final_event=completed_event,
                )
            return

        audit_log(
            "info",
            "chat.run_final",
            device_id_hash=hash_identifier(device_id),
            session_key_hash=hash_identifier(session_key),
            run_id_hash=hash_identifier(run_id),
            **_attachment_audit_fields(attachments),
            response_chars=len(str(response or "")),
            duration_ms=_elapsed_ms(started_at),
        )
        if idempotency_key:
            self._idempotency_cache.complete(
                device_id=device_id,
                session_key=session_key,
                idempotency_key=idempotency_key,
                run_id=run_id,
                final_event=completed_event,
            )

    def _record_chat_delivery_lost(
        self,
        *,
        device_id: str,
        session_key: str,
        run_id: str,
        started_at: float,
        delivery_state: str,
    ) -> None:
        audit_log(
            "info",
            "chat.run_delivery_lost",
            device_id_hash=hash_identifier(device_id),
            session_key_hash=hash_identifier(session_key),
            run_id_hash=hash_identifier(run_id),
            delivery_state=delivery_state,
            reason="websocket_disconnected",
            duration_ms=_elapsed_ms(started_at),
        )

    async def _send_duplicate_chat_response(
        self,
        ws: web.WebSocketResponse,
        rid: Any,
        entry: IdempotencyCacheEntry,
        *,
        device_id: str,
    ) -> None:
        if entry.state == "inflight":
            audit_log(
                "info",
                "chat.duplicate_inflight",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(entry.session_key),
                run_id_hash=hash_identifier(entry.run_id),
            )
            await _send_error(ws, rid, "BUSY_DUPLICATE", "duplicate request is already running")
            return

        audit_log(
            "info",
            "chat.duplicate_replayed",
            device_id_hash=hash_identifier(device_id),
            session_key_hash=hash_identifier(entry.session_key),
            run_id_hash=hash_identifier(entry.run_id),
        )
        status = "completed"
        if entry.final_event is not None:
            final_state = str(entry.final_event.get("payload", {}).get("state") or "")
            if final_state == "error":
                status = "error"
        await ws.send_json(
            {
                "type": "res",
                "id": rid,
                "ok": True,
                "payload": {
                    "runId": entry.run_id,
                    "status": status,
                    "duplicate": True,
                },
            }
        )
        if entry.final_event is not None:
            await ws.send_json(copy.deepcopy(entry.final_event))


def _hash_token(token: str, key: bytes) -> str:
    digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{DIGEST_PREFIX}{digest}"


def _legacy_hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_legacy_token_hash(token_hash: str) -> bool:
    if token_hash.startswith(DIGEST_PREFIX):
        return False
    if len(token_hash) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in token_hash)


def _validate_config(config: R1HermesConfig) -> None:
    if config.max_message_chars < 1:
        raise ValueError("max_message_chars must be at least 1")
    if config.per_device_concurrency < 1:
        raise ValueError("per_device_concurrency must be at least 1")
    if config.global_concurrency < 1:
        raise ValueError("global_concurrency must be at least 1")
    if config.rate_limit_messages < 1:
        raise ValueError("rate_limit_messages must be at least 1")
    if config.rate_limit_window_seconds < 1:
        raise ValueError("rate_limit_window_seconds must be at least 1")
    if config.unauthenticated_timeout_seconds < 1:
        raise ValueError("unauthenticated_timeout_seconds must be at least 1")
    if config.chat_run_timeout_seconds <= 0:
        raise ValueError("chat_run_timeout_seconds must be greater than 0")
    if config.chat_heartbeat_interval_seconds <= 0:
        raise ValueError("chat_heartbeat_interval_seconds must be greater than 0")
    if config.media_max_file_bytes < 1:
        raise ValueError("media_max_file_bytes must be at least 1")
    if config.media_ttl_seconds < 1:
        raise ValueError("media_ttl_seconds must be at least 1")


def _websocket_max_message_size(config: R1HermesConfig) -> int:
    media_budget = max(0, config.media_max_file_bytes * 2)
    return max(config.max_message_chars * 4, media_budget)


def _compose_hermes_prompt(text: str, media_files: tuple[StoredMediaFile, ...]) -> str:
    media_lines = [f"MEDIA:{media.path}" for media in media_files]
    prompt = text.strip()
    if media_lines and prompt:
        return "\n".join(media_lines) + "\n\n" + prompt
    if media_lines:
        return "\n".join(media_lines)
    return text


def _allowed_device_ids_from_env() -> frozenset[str] | None:
    return _normalize_allowed_device_ids(os.environ.get("R1_HERMES_ALLOWED_DEVICE_IDS"))


def _normalize_allowed_device_ids(
    value: str | list[str] | tuple[str, ...] | frozenset[str] | None,
) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidates = re.split(r"[\s,]+", value)
    else:
        candidates = value
    normalized = frozenset(device_id.strip() for device_id in candidates if device_id.strip())
    return normalized or None


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def _server_ssl_context(config: R1HermesConfig) -> ssl.SSLContext | None:
    if not config.tls_cert_file and not config.tls_key_file:
        return None
    if not config.tls_cert_file or not config.tls_key_file:
        raise ValueError("--tls-cert-file and --tls-key-file must be provided together")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.tls_cert_file, config.tls_key_file)
    return context


def _is_wildcard_public_bind(host: str) -> bool:
    value = host.strip()
    if not value:
        return True
    try:
        return _is_unspecified_bind_address(value)
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(
            value,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            flags=socket.AI_NUMERICHOST,
        )
    except socket.gaierror:
        return False
    return any(_is_unspecified_bind_address(result[4][0]) for result in addresses)


def _is_unspecified_bind_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.is_unspecified:
        return True
    return bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_unspecified)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def _attachment_audit_fields(attachments: Sequence[ImageAttachment]) -> dict[str, Any]:
    return {
        "attachment_count": len(attachments),
        "attachment_mime_types": [attachment.mime_type for attachment in attachments],
        "attachment_sizes": [attachment.size_bytes for attachment in attachments],
        "attachment_hashes": [
            attachment.content_hash or attachment.source_hash or "" for attachment in attachments
        ],
        "attachment_sources": [attachment.source_field for attachment in attachments],
    }


def _chat_final_event(
    run_id: str, session_key: str, response: str, *, seq: int = 2
) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "chat",
        "payload": {
            "runId": run_id,
            "sessionKey": session_key,
            "seq": seq,
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": str(response or "")}],
                "timestamp": _now_ms(),
            },
        },
    }


def _chat_running_event(run_id: str, session_key: str, *, seq: int) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "chat",
        "payload": {
            "runId": run_id,
            "sessionKey": session_key,
            "seq": seq,
            "state": "running",
            "heartbeat": True,
            "message": "Hermes is still working.",
            "timestamp": _now_ms(),
        },
    }


def _chat_error_event(
    run_id: str, session_key: str, error: ChatRunError, *, seq: int = 2
) -> dict[str, Any]:
    return {
        "type": "event",
        "event": "chat",
        "payload": {
            "runId": run_id,
            "sessionKey": session_key,
            "seq": seq,
            "state": "error",
            "error": {"code": error.code, "message": error.safe_message},
        },
    }


def _next_chat_seq(seq_ref: dict[str, int]) -> int:
    seq_ref["value"] += 1
    return seq_ref["value"]


async def _emit_chat_heartbeats(
    ws: web.WebSocketResponse,
    *,
    run_id: str,
    session_key: str,
    interval_seconds: float,
    next_seq: Callable[[], int],
    device_id: str,
    started_at: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        if _ws_closed(ws):
            return
        heartbeat = _chat_running_event(run_id, session_key, seq=next_seq())
        if not await _send_json_if_open(ws, heartbeat):
            audit_log(
                "info",
                "chat.run_cancelled",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                run_id_hash=hash_identifier(run_id),
                reason="websocket_disconnected",
                duration_ms=_elapsed_ms(started_at),
            )
            return


def _discard_finished_task(
    tasks: dict[asyncio.Task[None], bool], task: asyncio.Task[None]
) -> None:
    tasks.pop(task, None)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - defensive request-task boundary
        audit_log("warning", "chat.task_failed", error_type=exc.__class__.__name__)


async def _cancel_active_chat_tasks(tasks: dict[asyncio.Task[None], bool]) -> None:
    if not tasks:
        return
    pending = [
        task
        for task, cancel_on_disconnect in tasks.items()
        if cancel_on_disconnect and not task.done()
    ]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _chat_task_cancel_on_disconnect(
    frame: dict[str, Any],
    *,
    idempotency_cache_enabled: bool,
) -> bool:
    if not idempotency_cache_enabled:
        return True
    try:
        chat_request = parse_chat_send_params(request_params(frame))
    except PayloadParseError:
        return True
    return chat_request.idempotency_key is None


def _ws_closed(ws: web.WebSocketResponse) -> bool:
    return bool(getattr(ws, "closed", False))


async def _send_json_if_open(ws: web.WebSocketResponse, payload: dict[str, Any]) -> bool:
    if _ws_closed(ws):
        return False
    try:
        await ws.send_json(payload)
    except (ConnectionError, RuntimeError):
        return False
    return True


def _timestamp_from_json(data: dict[str, Any], key: str, *, default: int) -> int:
    if key not in data:
        return default
    try:
        value = int(data[key])
    except (TypeError, ValueError):
        return default
    return value or default


def _has_valid_timestamp(data: dict[str, Any], key: str) -> bool:
    if key not in data:
        return False
    try:
        return int(data[key]) > 0
    except (TypeError, ValueError):
        return False


def _is_ttl_expired(timestamp_ms: int, *, now_ms: int, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    return now_ms - timestamp_ms > ttl_seconds * 1000


def _peer_key(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername") if request.transport else None
    if isinstance(peername, tuple) and peername:
        host = peername[0]
        if isinstance(host, str) and host:
            return host
    if isinstance(request.remote, str) and request.remote:
        return request.remote
    return "unknown-peer"


def _is_local_request(request: web.Request) -> bool:
    peer = _peer_key(request)
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return peer.lower() == "localhost"


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


def _connect_ack_event(event: str, *, device_id: str) -> dict[str, Any]:
    return {
        "type": "event",
        "event": event,
        "payload": {
            "ok": True,
            "deviceId": device_id,
            "ts": _now_ms(),
        },
    }
