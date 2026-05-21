import argparse
import json
import logging

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from r1_hermes.adapter import R1HermesAdapter, R1HermesConfig
from r1_hermes.cli import add_server_args
from r1_hermes.frame_shape import build_frame_shape_log_fields

GATEWAY_TOKEN = "gateway-token-for-frame-shape-tests"
WRONG_TOKEN = "wrong-token-for-frame-shape-tests"
RAW_DEVICE_ID = "r1-raw-frame-shape-device"
RAW_PROMPT = "private prompt text that must never enter diagnostics"
RAW_BASE64 = "QXVkaW9CeXRlc1RoYXRMb29rTGlrZUJhc2U2NERhdGE="


class FakeHermesSink:
    def __init__(self):
        self.messages = []

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        self.messages.append({"text": text, "device_id": device_id, "session_key": session_key})
        return f"echo: {text}"


def audit_events(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "r1_hermes.audit"
    ]


def serialized_audit_logs(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


@pytest_asyncio.fixture
async def shape_logging_gateway(unused_tcp_port, tmp_path):
    sink = FakeHermesSink()
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=unused_tcp_port,
            gateway_token=GATEWAY_TOKEN,
            state_dir=tmp_path,
            frame_shape_logging=True,
        ),
        message_handler=sink,
    )
    await adapter.start()
    try:
        yield f"ws://127.0.0.1:{unused_tcp_port}/", sink
    finally:
        await adapter.stop()


async def ws_connect(url: str):
    session = ClientSession()
    ws = await session.ws_connect(url)
    challenge = await ws.receive_json()
    assert challenge["event"] == "connect.challenge"
    return session, ws


async def authenticate(ws, *, device_id: str = RAW_DEVICE_ID) -> str:
    await ws.send_json(
        {
            "type": "req",
            "id": "connect-shape",
            "method": "connect",
            "params": {
                "auth": {"token": GATEWAY_TOKEN},
                "device": {"id": device_id},
                "client": {"displayName": "Private Rabbit"},
            },
        }
    )
    hello = await ws.receive_json()
    assert hello["ok"] is True
    return str(hello["payload"]["auth"]["deviceToken"])


def unknown_media_frame(*, token: str | None = None) -> dict:
    params = {
        "device": {"id": RAW_DEVICE_ID},
        "prompt": RAW_PROMPT,
        "content": [
            {
                "type": "input_audio",
                "mimeType": "audio/wav",
                "data": RAW_BASE64,
            }
        ],
        "metadata": {"caption": RAW_PROMPT, "samples": [RAW_BASE64]},
        "attachments": [{"blobData": RAW_BASE64}],
    }
    if token is not None:
        params["auth"] = {"deviceToken": token}
    return {
        "type": "req",
        "id": "unknown-media-shape",
        "method": "media.upload",
        "params": params,
    }


def test_frame_shape_fields_report_structure_without_secret_values() -> None:
    fields = build_frame_shape_log_fields(
        unknown_media_frame(token="device-token-secret"),
        authenticated=True,
    )

    serialized = json.dumps(fields, sort_keys=True)

    assert fields["method"] == "media.upload"
    assert fields["frame_type"] == "req"
    assert fields["media_present"] is True
    assert "$.params.content[0].data" in fields["media_field_paths"]
    assert "$.params.attachments[0].blobData" in fields["media_field_paths"]
    assert fields["shape"]["keys"] == ["id", "method", "params", "type"]
    assert fields["shape"]["fields"]["params"]["fields"]["content"]["len"] == 1
    assert fields["shape"]["fields"]["params"]["fields"]["prompt"] == {
        "kind": "string",
        "chars": len(RAW_PROMPT),
    }
    assert fields["shape"]["fields"]["params"]["fields"]["content"]["items"][0][
        "fields"
    ]["type"] == {"kind": "string", "chars": len("input_audio"), "value": "input_audio"}

    assert "device-token-secret" not in serialized
    assert RAW_DEVICE_ID not in serialized
    assert RAW_PROMPT not in serialized
    assert RAW_BASE64 not in serialized


def test_frame_shape_fields_redact_token_like_method_keys_and_enum_values() -> None:
    token_like = "gateway-token-for-frame-shape-tests"

    fields = build_frame_shape_log_fields(
        {
            "type": token_like,
            "method": token_like,
            "params": {token_like: "value"},
        },
        authenticated=False,
    )

    serialized = json.dumps(fields, sort_keys=True)

    assert token_like not in serialized
    assert fields["method"] == f"[unsafe-string:chars={len(token_like)}]"
    assert fields["frame_type"] == f"[unsafe-string:chars={len(token_like)}]"
    assert f"[unsafe-key:chars={len(token_like)}]" in serialized

    device_id_fields = build_frame_shape_log_fields(
        {"type": RAW_DEVICE_ID, "method": "media.upload", "params": {}},
        authenticated=False,
    )
    assert RAW_DEVICE_ID not in json.dumps(device_id_fields, sort_keys=True)


def test_config_reads_frame_shape_logging_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.setenv("R1_HERMES_FRAME_SHAPE_LOGGING", "1")

    config = R1HermesConfig.from_env(state_dir=tmp_path)

    assert config.frame_shape_logging is True


def test_server_cli_accepts_frame_shape_logging_flag() -> None:
    parser = argparse.ArgumentParser()
    add_server_args(parser)

    args = parser.parse_args(["--frame-shape-logging"])

    assert args.frame_shape_logging is True


@pytest.mark.asyncio
async def test_known_text_chat_does_not_emit_frame_shape_logs_by_default(
    unused_tcp_port,
    tmp_path,
    caplog,
) -> None:
    sink = FakeHermesSink()
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=unused_tcp_port,
            gateway_token=GATEWAY_TOKEN,
            state_dir=tmp_path,
        ),
        message_handler=sink,
    )
    caplog.set_level(logging.INFO, logger="r1_hermes.audit")
    await adapter.start()
    try:
        session, ws = await ws_connect(f"ws://127.0.0.1:{unused_tcp_port}/")
        try:
            await authenticate(ws)
            await ws.send_json(
                {
                    "type": "req",
                    "id": "chat-shape-default",
                    "method": "chat.send",
                    "params": {
                        "message": "ordinary text chat",
                        "sessionKey": "main",
                        "idempotencyKey": "run-shape-default",
                    },
                }
            )
            assert (await ws.receive_json())["ok"] is True
            assert (await ws.receive_json())["payload"]["state"] == "started"
            assert (await ws.receive_json())["payload"]["state"] == "final"
        finally:
            await ws.close()
            await session.close()
    finally:
        await adapter.stop()

    assert sink.messages == [
        {"text": "ordinary text chat", "device_id": RAW_DEVICE_ID, "session_key": "main"}
    ]
    assert all(event["event"] != "frame.shape" for event in audit_events(caplog))


@pytest.mark.asyncio
async def test_unauthenticated_frame_shape_logging_redacts_auth_material_and_media_bytes(
    shape_logging_gateway,
    caplog,
) -> None:
    url, sink = shape_logging_gateway
    caplog.set_level(logging.INFO, logger="r1_hermes.audit")
    session, ws = await ws_connect(url)
    try:
        frame = unknown_media_frame(token=WRONG_TOKEN)
        await ws.send_json(frame)
        error = await ws.receive_json()
    finally:
        await ws.close()
        await session.close()

    assert error["ok"] is False
    assert error["error"]["code"] == "UNAUTHENTICATED"
    assert sink.messages == []

    logs = serialized_audit_logs(caplog)
    shape = next(event for event in audit_events(caplog) if event["event"] == "frame.shape")
    assert shape["phase"] == "unauthenticated"
    assert shape["method"] == "media.upload"
    assert shape["media_present"] is True
    assert "$.params.content[0].data" in shape["media_field_paths"]
    assert WRONG_TOKEN not in logs
    assert RAW_DEVICE_ID not in logs
    assert RAW_PROMPT not in logs
    assert RAW_BASE64 not in logs


@pytest.mark.asyncio
async def test_authenticated_unknown_media_method_logs_shape_and_fails_closed(
    shape_logging_gateway,
    caplog,
) -> None:
    url, sink = shape_logging_gateway
    caplog.set_level(logging.INFO, logger="r1_hermes.audit")
    session, ws = await ws_connect(url)
    try:
        device_token = await authenticate(ws)
        caplog.clear()

        frame = unknown_media_frame(token=device_token)
        await ws.send_json(frame)
        error = await ws.receive_json()
    finally:
        await ws.close()
        await session.close()

    assert error == {
        "type": "res",
        "id": "unknown-media-shape",
        "ok": False,
        "error": {"code": "UNKNOWN_METHOD", "message": "unsupported method"},
    }
    assert sink.messages == []

    logs = serialized_audit_logs(caplog)
    shape = next(event for event in audit_events(caplog) if event["event"] == "frame.shape")
    assert shape["phase"] == "authenticated"
    assert shape["method"] == "media.upload"
    assert shape["device_id_hash"].startswith("sha256:")
    assert shape["media_present"] is True
    assert "$.params.content[0].data" in shape["media_field_paths"]
    assert device_token not in logs
    assert RAW_DEVICE_ID not in logs
    assert RAW_PROMPT not in logs
    assert RAW_BASE64 not in logs
