from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

SECRET_REPLACEMENTS = {
    "DUMMY_GATEWAY_TOKEN_DO_NOT_USE": "[REDACTED]",
    "DUMMY_DEVICE_TOKEN_DO_NOT_USE": "[REDACTED]",
    "DUMMY_BINARY_DATA_OMITTED": "[REDACTED_MEDIA]",
    "cjEtaW1hZ2U=": "[REDACTED_MEDIA]",
}
FIXTURE_REPLAY_REPLACEMENTS = {
    "data:image/jpeg;base64,DUMMY_BINARY_DATA_OMITTED": "data:image/jpeg;base64,cjEtaW1hZ2U=",
}
MEDIA_DATA_KEYS = {"data", "b64_json", "base64", "bytes", "blob"}


@dataclass(frozen=True)
class FixtureReplayFlow:
    connect_fixture: str
    chat_fixture: str
    expected_device_id: str
    expected_message: str | None
    expected_session_key: str
    expected_run_id: str
    expected_ack_events: tuple[str, ...] = ()
    history_fixture: str | None = None
    expected_error_code: str | None = None
    expected_error_message: str | None = None
    connect_frame_id: str | None = None
    chat_frame_id: str | None = None
    history_frame_id: str | None = None


@dataclass(frozen=True)
class FixtureReplayResult:
    response_text: str | None
    run_id: str | None
    device_token: str = field(repr=False)
    frames: tuple[dict[str, Any], ...]
    error_code: str | None = None
    error_message: str | None = None

    @property
    def serialized_frames(self) -> str:
        return json.dumps(self.frames, sort_keys=True)

    @property
    def serialized_chat_frames(self) -> str:
        chat_frames = [
            frame
            for frame in self.frames
            if frame.get("phase") in {"chat_ack", "chat_started", "chat_final"}
        ]
        return json.dumps(chat_frames, sort_keys=True)


async def replay_fixture_flow(
    *,
    url: str,
    fixture_dir: Path,
    flow: FixtureReplayFlow,
    gateway_token: str,
) -> FixtureReplayResult:
    frames: list[dict[str, Any]] = []
    connect_frame = _load_fixture(
        fixture_dir,
        flow.connect_fixture,
        frame_id=flow.connect_frame_id,
    )
    chat_frame = _hydrate_media_placeholders(
        _load_fixture(fixture_dir, flow.chat_fixture, frame_id=flow.chat_frame_id)
    )
    history_frame = (
        _load_fixture(fixture_dir, flow.history_fixture, frame_id=flow.history_frame_id)
        if flow.history_fixture
        else None
    )
    safe_connect_frame = _safe_frame(connect_frame)
    connect_frame = _replace_text(connect_frame, {"DUMMY_GATEWAY_TOKEN_DO_NOT_USE": gateway_token})

    async with ClientSession() as session:
        async with session.ws_connect(url) as ws:
            challenge = await ws.receive_json()
            frames.append({"phase": "challenge", "frame": challenge})
            assert challenge["event"] == "connect.challenge"

            frames.append({"phase": "connect_request", "frame": safe_connect_frame})
            await ws.send_json(connect_frame)
            hello = await ws.receive_json()
            frames.append({"phase": "connect_response", "frame": _safe_frame(hello)})
            assert hello["ok"] is True
            device_token = str(hello["payload"]["auth"]["deviceToken"])
            assert device_token

            for expected_event in flow.expected_ack_events:
                event = await ws.receive_json()
                frames.append({"phase": "connect_ack_event", "frame": _safe_frame(event)})
                assert event["event"] == expected_event
                assert event["payload"]["deviceId"] == flow.expected_device_id

            if history_frame is not None:
                frames.append(
                    {"phase": "history_before_request", "frame": _safe_frame(history_frame)}
                )
                await ws.send_json(
                    _replace_text(history_frame, {"DUMMY_DEVICE_TOKEN_DO_NOT_USE": device_token})
                )
                history_before = await ws.receive_json()
                frames.append(
                    {"phase": "history_before_response", "frame": _safe_frame(history_before)}
                )
                _assert_history_unsupported(history_before, session_key=flow.expected_session_key)

            frames.append({"phase": "chat_request", "frame": _safe_frame(chat_frame)})
            outbound_chat_frame = _replace_text(
                chat_frame, {"DUMMY_DEVICE_TOKEN_DO_NOT_USE": device_token}
            )
            await ws.send_json(outbound_chat_frame)

            chat_ack = await ws.receive_json()
            if flow.expected_error_code is not None:
                frames.append({"phase": "chat_error", "frame": _safe_frame(chat_ack)})
                assert chat_ack == {
                    "type": "res",
                    "id": chat_frame["id"],
                    "ok": False,
                    "error": {
                        "code": flow.expected_error_code,
                        "message": flow.expected_error_message,
                    },
                }
                return FixtureReplayResult(
                    response_text=None,
                    run_id=None,
                    device_token=device_token,
                    frames=tuple(frames),
                    error_code=str(chat_ack["error"]["code"]),
                    error_message=str(chat_ack["error"]["message"]),
                )

            started = await ws.receive_json()
            final = await ws.receive_json()
            frames.extend(
                [
                    {"phase": "chat_ack", "frame": _safe_frame(chat_ack)},
                    {"phase": "chat_started", "frame": _safe_frame(started)},
                    {"phase": "chat_final", "frame": _safe_frame(final)},
                ]
            )

            if history_frame is not None:
                frames.append(
                    {"phase": "history_after_request", "frame": _safe_frame(history_frame)}
                )
                await ws.send_json(
                    _replace_text(history_frame, {"DUMMY_DEVICE_TOKEN_DO_NOT_USE": device_token})
                )
                history_after = await ws.receive_json()
                frames.append(
                    {"phase": "history_after_response", "frame": _safe_frame(history_after)}
                )
                _assert_history_unsupported(history_after, session_key=flow.expected_session_key)

    assert chat_ack["ok"] is True
    assert chat_ack["payload"]["runId"] == flow.expected_run_id
    assert started["payload"] == {
        "runId": flow.expected_run_id,
        "sessionKey": flow.expected_session_key,
        "seq": 1,
        "state": "started",
    }
    assert final["payload"]["runId"] == flow.expected_run_id
    assert final["payload"]["sessionKey"] == flow.expected_session_key
    assert final["payload"]["state"] == "final"
    response_text = _extract_response_text(final)
    return FixtureReplayResult(
        response_text=response_text,
        run_id=str(chat_ack["payload"]["runId"]),
        device_token=device_token,
        frames=tuple(frames),
    )


def _load_fixture(fixture_dir: Path, name: str, *, frame_id: str | None = None) -> dict[str, Any]:
    data = json.loads((fixture_dir / name).read_text())
    if isinstance(data, dict) and isinstance(data.get("frames"), list):
        if frame_id is None:
            raise AssertionError(f"flow fixture requires frame_id: {name}")
        for frame in data["frames"]:
            if isinstance(frame, dict) and frame.get("id") == frame_id:
                return frame
        raise AssertionError(f"frame id {frame_id!r} not found in fixture: {name}")
    if not isinstance(data, dict):
        raise AssertionError(f"fixture must be a JSON object: {name}")
    return data


def _safe_frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    return _redact_secrets(dict(frame))


def _hydrate_media_placeholders(frame: dict[str, Any]) -> dict[str, Any]:
    return _replace_text(frame, FIXTURE_REPLAY_REPLACEMENTS)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted = {}
        for key, child in value.items():
            if key in {"token", "authToken", "gatewayToken", "deviceToken", "bearerToken"}:
                redacted[key] = "[REDACTED]"
            elif key in MEDIA_DATA_KEYS:
                redacted[key] = "[REDACTED_MEDIA]"
            else:
                redacted[key] = _redact_secrets(child)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str):
        for secret, replacement in SECRET_REPLACEMENTS.items():
            value = value.replace(secret, replacement)
    return value


def _replace_text(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {key: _replace_text(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_text(item, replacements) for item in value]
    if isinstance(value, str):
        for needle, replacement in replacements.items():
            value = value.replace(needle, replacement)
    return value


def _extract_response_text(event: Mapping[str, Any]) -> str:
    message = (event.get("payload") or {}).get("message") or {}
    content = message.get("content") or []
    parts = []
    for item in content:
        if isinstance(item, Mapping) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


def _assert_history_unsupported(frame: Mapping[str, Any], *, session_key: str) -> None:
    assert frame == {
        "type": "res",
        "id": "history-sample-001",
        "ok": True,
        "payload": {
            "sessionKey": session_key,
            "messages": [],
            "status": "unsupported",
            "historySupported": False,
            "storage": "none",
        },
    }
