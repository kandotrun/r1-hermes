import json
import stat

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType

from r1_hermes.adapter import R1HermesAdapter, R1HermesConfig


class FakeHermesSink:
    def __init__(self):
        self.messages = []

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        self.messages.append({"text": text, "device_id": device_id, "session_key": session_key})
        return f"echo: {text}"


class RaisingHermesSink:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.messages = []

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        self.messages.append({"text": text, "device_id": device_id, "session_key": session_key})
        raise self.exc


@pytest_asyncio.fixture
async def running_adapter(unused_tcp_port, tmp_path):
    sink = FakeHermesSink()
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
            per_device_concurrency=1,
            rate_limit_messages=2,
            rate_limit_window_seconds=60,
        ),
        message_handler=sink,
    )
    await adapter.start()
    try:
        yield adapter, sink, f"http://127.0.0.1:{port}"
    finally:
        await adapter.stop()


async def ws_connect(base_url: str):
    session = ClientSession()
    ws = await session.ws_connect(base_url.replace("http", "ws") + "/")
    challenge = await ws.receive_json()
    assert challenge["event"] == "connect.challenge"
    return session, ws


@pytest.mark.asyncio
async def test_chat_send_before_connect_is_rejected_and_does_not_run_agent(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {"type": "req", "id": "1", "method": "chat.send", "params": {"message": "hi"}}
        )
        msg = await ws.receive_json()
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHENTICATED"
        assert sink.messages == []
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_connect_requires_gateway_token(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {"auth": {"token": "wrong"}, "device": {"id": "r1-test"}},
            }
        )
        msg = await ws.receive_json()
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        close = await ws.receive()
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        assert sink.messages == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_connect_accepts_sanitized_payload_aliases(running_adapter):
    _adapter, _sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "payload": {
                    "authToken": "gateway-token-for-tests",
                    "deviceId": "r1-alias",
                    "client": {"name": "OpenClaw"},
                    "ignored": "field",
                },
            }
        )

        hello = await ws.receive_json()
        assert hello["ok"] is True
        assert hello["payload"]["auth"]["deviceToken"]
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_authenticated_chat_send_runs_agent_once(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-test"},
                    "client": {"displayName": "Rabbit R1"},
                },
            }
        )
        hello = await ws.receive_json()
        assert hello["ok"] is True
        device_token = hello["payload"]["auth"]["deviceToken"]
        assert device_token

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "params": {"message": "hello", "sessionKey": "main", "idempotencyKey": "run-1"},
            }
        )
        ack = await ws.receive_json()
        assert ack["ok"] is True
        started = await ws.receive_json()
        final = await ws.receive_json()
        assert started["type"] == "event"
        assert started["event"] == "chat"
        assert started["payload"] == {
            "runId": "run-1",
            "sessionKey": "main",
            "seq": 1,
            "state": "started",
        }
        assert final["type"] == "event"
        assert final["event"] == "chat"
        assert final["payload"]["runId"] == "run-1"
        assert final["payload"]["sessionKey"] == "main"
        assert final["payload"]["seq"] == 2
        assert final["payload"]["state"] == "final"
        assert final["payload"]["message"]["content"][0]["text"] == "echo: hello"
        assert sink.messages == [{"text": "hello", "device_id": "r1-test", "session_key": "main"}]
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_message"),
    [
        (
            RuntimeError("DUMMY_SECRET_TOKEN_DO_NOT_USE failure details"),
            "CHAT_RUN_FAILED",
            "chat run failed",
        ),
        (
            TimeoutError("DUMMY_GATEWAY_TOKEN_DO_NOT_USE timed out"),
            "CHAT_RUN_TIMEOUT",
            "chat run timed out",
        ),
    ],
)
async def test_chat_handler_errors_emit_generic_error_event_without_token_leak(
    unused_tcp_port,
    tmp_path,
    exc,
    expected_code,
    expected_message,
):
    sink = RaisingHermesSink(exc)
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
        ),
        message_handler=sink,
    )
    await adapter.start()
    session = None
    try:
        base_url = f"http://127.0.0.1:{port}"
        session, ws = await ws_connect(base_url)
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-error"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-error",
                "method": "chat.send",
                "params": {
                    "message": "trigger failure",
                    "sessionKey": "main",
                    "idempotencyKey": "run-error",
                },
            }
        )

        ack = await ws.receive_json()
        started = await ws.receive_json()
        error = await ws.receive_json()
        serialized = json.dumps([ack, started, error])

        assert ack["ok"] is True
        assert started["payload"] == {
            "runId": "run-error",
            "sessionKey": "main",
            "seq": 1,
            "state": "started",
        }
        assert error["type"] == "event"
        assert error["event"] == "chat"
        assert error["payload"] == {
            "runId": "run-error",
            "sessionKey": "main",
            "seq": 2,
            "state": "error",
            "error": {"code": expected_code, "message": expected_message},
        }
        assert "gateway-token-for-tests" not in serialized
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in serialized
        assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in serialized
        assert sink.messages == [
            {"text": "trigger failure", "device_id": "r1-error", "session_key": "main"}
        ]
    finally:
        if session is not None:
            await ws.close()
            await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_chat_send_accepts_payload_aliases_without_exposing_device_token(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-alias-chat"},
                },
            }
        )
        hello = await ws.receive_json()
        device_token = hello["payload"]["auth"]["deviceToken"]

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-1",
                "method": "chat.send",
                "payload": {
                    "text": "hello aliases",
                    "session": {"id": "alias-session"},
                    "requestId": "run-alias",
                    "auth": {"deviceToken": device_token},
                },
            }
        )

        ack = await ws.receive_json()
        started = await ws.receive_json()
        final = await ws.receive_json()
        serialized = json.dumps([ack, started, final])
        assert ack["ok"] is True
        assert ack["payload"]["runId"] == "run-alias"
        assert started["payload"]["state"] == "started"
        assert final["payload"]["state"] == "final"
        assert device_token not in serialized
        assert sink.messages == [
            {"text": "hello aliases", "device_id": "r1-alias-chat", "session_key": "alias-session"}
        ]
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_device_token_is_bound_to_original_device_id(running_adapter):
    adapter, _sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-original"},
                },
            }
        )
        hello = await ws.receive_json()
        device_token = hello["payload"]["auth"]["deviceToken"]
    finally:
        await ws.close()
        await session.close()

    session2, ws2 = await ws_connect(base_url)
    try:
        await ws2.send_json(
            {
                "type": "req",
                "id": "connect-2",
                "method": "connect",
                "params": {"auth": {"token": device_token}, "device": {"id": "r1-attacker"}},
            }
        )
        msg = await ws2.receive_json()
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert "r1-attacker" not in adapter.state.devices
    finally:
        await session2.close()


@pytest.mark.asyncio
async def test_malformed_json_shape_is_rejected_and_does_not_run_agent(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_str(json.dumps(["not", "an", "object"]))
        msg = await ws.receive_json()
        assert msg["ok"] is False
        assert msg["error"]["code"] == "BAD_REQUEST"
        assert sink.messages == []
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_malformed_chat_payload_is_rejected_without_token_leak_or_agent_run(
    running_adapter,
):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-malformed"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-malformed",
                "method": "chat.send",
                "payload": ["DUMMY_DEVICE_TOKEN_DO_NOT_USE"],
            }
        )
        msg = await ws.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "BAD_REQUEST"
        assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in serialized
        assert sink.messages == []
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_unknown_method_does_not_echo_secret_or_run_agent(running_adapter):
    _adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-unknown"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True

        await ws.send_json(
            {
                "type": "req",
                "id": "unknown-1",
                "method": "DUMMY_DEVICE_TOKEN_DO_NOT_USE",
                "params": {"message": "should not run"},
            }
        )
        msg = await ws.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNKNOWN_METHOD"
        assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in serialized
        assert sink.messages == []
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_root_http_does_not_expose_admin_or_tokens(running_adapter):
    _adapter, _sink, base_url = running_adapter
    async with ClientSession() as session:
        async with session.get(base_url + "/") as response:
            assert response.status == 404
            text = await response.text()
            assert "gateway-token-for-tests" not in text
            assert "deviceToken" not in text


@pytest.mark.asyncio
async def test_rate_limit_rejects_excess_messages(running_adapter):
    _adapter, _sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-test"},
                },
            }
        )
        assert (await ws.receive_json())["ok"] is True
        for i in range(2):
            await ws.send_json(
                {
                    "type": "req",
                    "id": f"chat-{i}",
                    "method": "chat.send",
                    "params": {"message": f"hello {i}", "idempotencyKey": f"run-{i}"},
                }
            )
            assert (await ws.receive_json())["ok"] is True
            assert (await ws.receive_json())["event"] == "chat"
            assert (await ws.receive_json())["event"] == "chat"

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-over",
                "method": "chat.send",
                "params": {"message": "too many", "idempotencyKey": "run-over"},
            }
        )
        msg = await ws.receive_json()
        assert msg["ok"] is False
        assert msg["error"]["code"] == "RATE_LIMITED"
    finally:
        await ws.close()
        await session.close()


def test_pairing_payload_is_explicitly_secret():
    from r1_hermes.qr import build_pairing_payload

    payload = build_pairing_payload(hosts=["100.64.0.1"], port=18789, token="secret", protocol="ws")
    decoded = json.loads(payload)
    assert decoded == {
        "type": "clawdbot-gateway",
        "version": 1,
        "ips": ["100.64.0.1"],
        "port": 18789,
        "token": "secret",
        "protocol": "ws",
    }


def test_device_state_permissions_are_owner_only(tmp_path):
    from r1_hermes.adapter import DeviceState

    state = DeviceState(tmp_path / "state")
    token = state.issue_device_token("r1-test")
    assert token
    assert stat.S_IMODE(state.state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
