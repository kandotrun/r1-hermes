import json

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType

from r1_hermes.adapter import R1HermesConfig
from r1_hermes.native_gateway import R1GatewayMessageBridge, R1NativeGatewayAdapter


class FakeGatewayPipeline:
    def __init__(self, response_text="native reply"):
        self.response_text = response_text
        self.events = []

    async def __call__(self, event):
        self.events.append(event)
        return self.response_text


@pytest_asyncio.fixture
async def running_native_adapter(unused_tcp_port, tmp_path):
    pipeline = FakeGatewayPipeline()
    port = unused_tcp_port
    adapter = R1NativeGatewayAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-native-tests",
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
    challenge = await ws.receive_json()
    assert challenge["event"] == "connect.challenge"
    return session, ws


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
    }
    assert "gateway-token" not in repr(event)
    assert "deviceToken" not in repr(event)


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
                    "auth": {"token": "wrong-token"},
                    "device": {"id": "r1-native-auth"},
                },
            }
        )
        msg = await ws.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert "wrong-token" not in serialized
        assert pipeline.events == []
        close = await ws.receive()
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
            gateway_token="gateway-token-for-native-tests",
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
                    "auth": {"token": "gateway-token-for-native-tests"},
                    "device": {"id": "r1-blocked"},
                },
            }
        )
        msg = await ws.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert "gateway-token-for-native-tests" not in serialized
        assert "r1-blocked" not in adapter.state.devices
        assert pipeline.events == []
    finally:
        if session is not None:
            await session.close()
        await adapter.stop()


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
                    "auth": {"token": "gateway-token-for-native-tests"},
                    "device": {"id": "r1-native-chat"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True

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

        ack = await ws.receive_json()
        started = await ws.receive_json()
        final = await ws.receive_json()
        serialized = json.dumps([ack, started, final])

        assert ack["ok"] is True
        assert started["payload"]["state"] == "started"
        assert final["type"] == "event"
        assert final["event"] == "chat"
        assert final["payload"]["runId"] == "native-run-1"
        assert final["payload"]["sessionKey"] == "r1-session"
        assert final["payload"]["state"] == "final"
        assert final["payload"]["message"]["content"][0]["text"] == "native reply"
        assert "gateway-token-for-native-tests" not in serialized
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
                    "auth": {"token": "gateway-token-for-native-tests"},
                    "device": {"id": "r1-native-send"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True
        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {
                    "message": "activate session",
                    "sessionKey": "main",
                    "idempotencyKey": "native-run-send",
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True
        assert (await ws.receive_json())["payload"]["state"] == "started"
        assert (await ws.receive_json())["payload"]["state"] == "final"

        assert (
            await adapter.send_text(
                device_id="r1-native-send",
                session_key="main",
                text="proactive native reply",
                run_id="native-proactive-1",
            )
            is True
        )
        event = await ws.receive_json()
        serialized = json.dumps(event)
        assert event["type"] == "event"
        assert event["event"] == "chat"
        assert event["payload"]["runId"] == "native-proactive-1"
        assert event["payload"]["sessionKey"] == "main"
        assert event["payload"]["state"] == "final"
        assert event["payload"]["message"]["content"][0]["text"] == "proactive native reply"
        assert "gateway-token-for-native-tests" not in serialized
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
