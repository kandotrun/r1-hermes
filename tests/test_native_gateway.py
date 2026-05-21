import asyncio
import json
import logging

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType

from r1_hermes.adapter import R1HermesConfig
from r1_hermes.native_gateway import R1GatewayMessageBridge, R1NativeGatewayAdapter

from .token_fixtures import STRONG_GATEWAY_TOKEN, WRONG_STRONG_GATEWAY_TOKEN


class FakeGatewayPipeline:
    def __init__(self, response_text="native reply"):
        self.response_text = response_text
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return self.response_text


def audit_events(caplog):
    return [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "r1_hermes.audit"
    ]


def serialized_audit_logs(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


@pytest_asyncio.fixture
async def running_native_adapter(unused_tcp_port, tmp_path):
    pipeline = FakeGatewayPipeline()
    port = unused_tcp_port
    adapter = R1NativeGatewayAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token=STRONG_GATEWAY_TOKEN,
            state_dir=tmp_path,
            max_message_chars=128,
        ),
        gateway_message_handler=pipeline,
        platform_toolsets=("safe", "web"),
    )
    await adapter.start()
    try:
        yield adapter, pipeline, f"http://127.0.0.1:{port}"
    finally:
        await adapter.stop()


async def ws_connect(base_url: str):
    session = ClientSession()
    ws = await session.ws_connect(base_url.replace("http", "ws") + "/")
    challenge = await receive_json(ws)
    assert challenge["event"] == "connect.challenge"
    return session, ws


async def receive_json(ws, *, timeout: float = 5.0):
    return await asyncio.wait_for(ws.receive_json(), timeout=timeout)


def test_chat_send_becomes_gateway_message_event_without_secret_metadata():
    bridge = R1GatewayMessageBridge(
        gateway_message_handler=FakeGatewayPipeline(),
        platform_toolsets=("safe",),
    )

    event = bridge.to_message_event(
        "hello native gateway",
        device_id="r1-native-unit",
        session_key="main",
    )

    assert event.platform == "rabbit_r1"
    assert event.user_id == "r1-native-unit"
    assert event.source == "rabbit_r1:r1-native-unit:main"
    assert event.text == "hello native gateway"
    assert event.session_id == "r1:r1-native-unit:main"
    assert event.metadata == {
        "device_id": "r1-native-unit",
        "session_key": "main",
        "platform_toolsets": ("safe",),
        "attachment_count": 0,
    }
    assert "gateway-token" not in repr(event)
    assert "deviceToken" not in repr(event)


def test_native_gateway_platform_toolsets_drop_high_impact_metadata_by_default():
    bridge = R1GatewayMessageBridge(
        gateway_message_handler=FakeGatewayPipeline(),
        platform_toolsets=("safe", "terminal", "file", "web"),
    )

    event = bridge.to_message_event(
        "hello native gateway",
        device_id="r1-native-unit",
        session_key="main",
    )

    assert bridge.platform_toolsets == ("safe", "web")
    assert event.metadata["platform_toolsets"] == ("safe", "web")


def test_native_gateway_platform_toolsets_allow_high_impact_metadata_with_override():
    bridge = R1GatewayMessageBridge(
        gateway_message_handler=FakeGatewayPipeline(),
        platform_toolsets=("safe", "terminal", "file"),
        allow_high_impact_toolsets=True,
    )

    event = bridge.to_message_event(
        "hello native gateway",
        device_id="r1-native-unit",
        session_key="main",
    )

    assert event.metadata["platform_toolsets"] == ("safe", "terminal", "file")


@pytest.mark.asyncio
async def test_unexpected_gateway_reply_shape_is_not_stringified_with_secrets():
    class UnexpectedGatewayReply:
        def __repr__(self):
            return "UnexpectedGatewayReply(token=DUMMY_GATEWAY_TOKEN_DO_NOT_USE)"

    async def fake_gateway_handler(_event):
        return UnexpectedGatewayReply()

    bridge = R1GatewayMessageBridge(gateway_message_handler=fake_gateway_handler)

    response = await bridge(
        "hello",
        device_id="r1-native-unit",
        session_key="main",
    )

    assert response == "Gateway returned an unsupported response."
    assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in response


@pytest.mark.asyncio
async def test_native_adapter_auth_rejects_before_gateway_pipeline(running_native_adapter):
    _adapter, pipeline, base_url = running_native_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": WRONG_STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-native-auth"},
                },
            }
        )
        msg = await receive_json(ws)
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert WRONG_STRONG_GATEWAY_TOKEN not in serialized
        assert pipeline.events == []
        close = await asyncio.wait_for(ws.receive(), timeout=5.0)
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_native_adapter_allowed_device_policy_blocks_unlisted_devices(
    unused_tcp_port,
    tmp_path,
):
    pipeline = FakeGatewayPipeline()
    port = unused_tcp_port
    adapter = R1NativeGatewayAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token=STRONG_GATEWAY_TOKEN,
            state_dir=tmp_path,
        ),
        gateway_message_handler=pipeline,
        allowed_device_ids=("r1-allowed",),
    )
    await adapter.start()
    session = None
    try:
        session, ws = await ws_connect(f"http://127.0.0.1:{port}")
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-blocked"},
                },
            }
        )
        msg = await receive_json(ws)
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert STRONG_GATEWAY_TOKEN not in serialized
        assert "r1-blocked" not in adapter.state.devices
        assert pipeline.events == []
    finally:
        if session is not None:
            await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_native_adapter_rejects_second_connect_without_changing_active_device(
    running_native_adapter,
):
    adapter, _pipeline, base_url = running_native_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-original"},
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True

        await ws.send_json(
            {
                "type": "req",
                "id": "connect-2",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-replacement"},
                },
            }
        )
        msg = await receive_json(ws)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "ALREADY_CONNECTED"

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-original",
                "method": "chat.send",
                "params": {
                    "message": "activate original",
                    "sessionKey": "main",
                    "idempotencyKey": "native-run-original",
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        assert (await receive_json(ws))["payload"]["state"] == "started"
        assert (await receive_json(ws))["payload"]["state"] == "final"

        assert (
            await adapter.send_text(
                device_id="r1-original",
                session_key="main",
                text="original still active",
            )
            is True
        )
        assert (await receive_json(ws))["payload"]["message"]["content"][0]["text"] == (
            "original still active"
        )
        assert (
            await adapter.send_text(
                device_id="r1-replacement",
                session_key="main",
                text="must not send",
            )
            is False
        )
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_native_adapter_chat_send_uses_gateway_pipeline_and_returns_reply_event(
    running_native_adapter,
):
    _adapter, pipeline, base_url = running_native_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-native-chat"},
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {
                    "message": "hello via native pipeline",
                    "sessionKey": "r1-session",
                    "idempotencyKey": "native-run-1",
                },
            }
        )

        ack = await receive_json(ws)
        started = await receive_json(ws)
        final = await receive_json(ws)
        serialized = json.dumps([ack, started, final])

        assert ack["ok"] is True
        assert started["payload"]["state"] == "started"
        assert final["type"] == "event"
        assert final["event"] == "chat"
        assert final["payload"]["runId"] == "native-run-1"
        assert final["payload"]["sessionKey"] == "r1-session"
        assert final["payload"]["state"] == "final"
        assert final["payload"]["message"]["content"][0]["text"] == "native reply"
        assert STRONG_GATEWAY_TOKEN not in serialized
        assert len(pipeline.events) == 1
        event = pipeline.events[0]
        assert event.text == "hello via native pipeline"
        assert event.user_id == "r1-native-chat"
        assert event.source == "rabbit_r1:r1-native-chat:r1-session"
        assert event.metadata["platform_toolsets"] == ("safe", "web")
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_native_send_is_noop_without_active_socket_and_emits_on_active_session(
    running_native_adapter,
):
    adapter, _pipeline, base_url = running_native_adapter
    assert (
        await adapter.send_text(
            device_id="r1-missing",
            session_key="main",
            text="offline message",
        )
        is False
    )

    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-native-send"},
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {
                    "message": "activate session",
                    "sessionKey": "main/admin",
                    "idempotencyKey": "native-run-send",
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        assert (await receive_json(ws))["payload"]["state"] == "started"
        assert (await receive_json(ws))["payload"]["state"] == "final"

        assert (
            await adapter.send_text(
                device_id="r1-native-send",
                session_key="main/admin",
                text="proactive native reply",
                run_id="native-proactive-1",
            )
            is True
        )
        event = await receive_json(ws)
        serialized = json.dumps(event)
        assert event["type"] == "event"
        assert event["event"] == "chat"
        assert event["payload"]["runId"] == "native-proactive-1"
        assert event["payload"]["sessionKey"] == "main-admin"
        assert event["payload"]["state"] == "final"
        assert event["payload"]["message"]["content"][0]["text"] == "proactive native reply"
        assert STRONG_GATEWAY_TOKEN not in serialized
    finally:
        await ws.close()
        await session.close()

    assert (
        await adapter.send_text(
            device_id="r1-native-send",
            session_key="main",
            text="after close",
        )
        is False
    )


@pytest.mark.asyncio
async def test_native_send_truncates_oversized_text_without_output_dump(
    unused_tcp_port,
    tmp_path,
    caplog,
):
    pipeline = FakeGatewayPipeline()
    port = unused_tcp_port
    adapter = R1NativeGatewayAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token=STRONG_GATEWAY_TOKEN,
            state_dir=tmp_path,
            outbound_text_max_chars=80,
            outbound_event_max_bytes=4096,
        ),
        gateway_message_handler=pipeline,
    )
    caplog.set_level(logging.INFO, logger="r1_hermes.audit")
    await adapter.start()
    session, ws = await ws_connect(f"http://127.0.0.1:{port}")
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-native-truncate"},
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {
                    "message": "activate session",
                    "sessionKey": "main",
                    "idempotencyKey": "native-truncate-run",
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        assert (await receive_json(ws))["payload"]["state"] == "started"
        assert (await receive_json(ws))["payload"]["state"] == "final"

        private_text = "native private output DUMMY_SECRET_TOKEN_DO_NOT_USE " + ("x" * 120)
        assert (
            await adapter.send_text(
                device_id="r1-native-truncate",
                session_key="main",
                text=private_text,
                run_id="native-truncate-1",
            )
            is True
        )

        event = await receive_json(ws)
        text = event["payload"]["message"]["content"][0]["text"]
        logs = serialized_audit_logs(caplog)
        audit = next(
            event for event in audit_events(caplog) if event["event"] == "native.send_truncated"
        )

        assert event["payload"]["state"] == "final"
        assert len(text) <= 80
        assert "truncated" in text
        assert event["payload"]["outbound"] == {
            "truncated": True,
            "originalChars": len(private_text),
            "returnedChars": len(text),
            "maxChars": 80,
        }
        assert audit["original_chars"] == len(private_text)
        assert audit["returned_chars"] == len(text)
        assert audit["device_id_hash"].startswith("sha256:")
        assert "native private output" not in json.dumps(event)
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in json.dumps(event)
        assert "native private output" not in logs
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in logs
        assert "r1-native-truncate" not in logs
    finally:
        await ws.close()
        await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_native_send_over_event_cap_returns_safe_error_without_text_leak(
    unused_tcp_port,
    tmp_path,
    caplog,
):
    pipeline = FakeGatewayPipeline()
    port = unused_tcp_port
    adapter = R1NativeGatewayAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token=STRONG_GATEWAY_TOKEN,
            state_dir=tmp_path,
            outbound_text_max_chars=1000,
            outbound_event_max_bytes=320,
        ),
        gateway_message_handler=pipeline,
    )
    caplog.set_level(logging.INFO, logger="r1_hermes.audit")
    await adapter.start()
    session, ws = await ws_connect(f"http://127.0.0.1:{port}")
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": STRONG_GATEWAY_TOKEN},
                    "device": {"id": "r1-native-event-cap"},
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {
                    "message": "activate session",
                    "sessionKey": "main",
                    "idempotencyKey": "native-event-cap-run",
                },
            }
        )
        assert (await receive_json(ws))["ok"] is True
        assert (await receive_json(ws))["payload"]["state"] == "started"
        assert (await receive_json(ws))["payload"]["state"] == "final"

        private_text = "native huge output DUMMY_SECRET_TOKEN_DO_NOT_USE " + ("x" * 2000)
        assert (
            await adapter.send_text(
                device_id="r1-native-event-cap",
                session_key="main",
                text=private_text,
                run_id="native-event-cap-1",
            )
            is True
        )

        event = await receive_json(ws)
        serialized = json.dumps(event)
        logs = serialized_audit_logs(caplog)
        audit = next(
            event
            for event in audit_events(caplog)
            if event["event"] == "native.outbound_event_too_large"
        )

        assert event["payload"] == {
            "runId": "native-event-cap-1",
            "sessionKey": "main",
            "seq": 1,
            "state": "error",
            "error": {
                "code": "CHAT_OUTPUT_TOO_LARGE",
                "message": "chat response exceeded the outbound size limit",
            },
        }
        assert len(serialized.encode("utf-8")) <= 320
        assert audit["outbound_event_max_bytes"] == 320
        assert audit["response_chars"] == len(private_text)
        assert audit["candidate_event_bytes"] > 320
        assert "native huge output" not in serialized
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in serialized
        assert "native huge output" not in logs
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in logs
    finally:
        await ws.close()
        await session.close()
        await adapter.stop()
