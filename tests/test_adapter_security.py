import asyncio
import hashlib
import json
import stat
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType

from r1_hermes import adapter as adapter_module
from r1_hermes.adapter import DeviceState, R1HermesAdapter, R1HermesConfig

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r1_payloads"
WILDCARD_IPV4 = ".".join(("0", "0", "0", "0"))


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


class BlockingHermesSink:
    def __init__(self):
        self.messages = []
        self.release = asyncio.Event()
        self._started = asyncio.Condition()

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        async with self._started:
            self.messages.append(
                {"text": text, "device_id": device_id, "session_key": session_key}
            )
            self._started.notify_all()
        await self.release.wait()
        return f"released: {text}"

    async def wait_for_calls(self, count: int) -> None:
        async with self._started:
            await self._started.wait_for(lambda: len(self.messages) >= count)


class FakeWebSocket:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)


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


@pytest_asyncio.fixture
async def unauth_limited_adapter(unused_tcp_port, tmp_path):
    sink = FakeHermesSink()
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
            unauthenticated_connection_limit=1,
            unauthenticated_attempt_limit=2,
            unauthenticated_attempt_window_seconds=60,
            unauthenticated_cooldown_seconds=60,
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


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def fixture_with_gateway_token(frame: dict):
    serialized = json.dumps(frame)
    return json.loads(
        serialized.replace("DUMMY_GATEWAY_TOKEN_DO_NOT_USE", "gateway-token-for-tests")
    )


async def authenticated_ws(base_url: str, *, device_id: str):
    session, ws = await ws_connect(base_url)
    await ws.send_json(
        {
            "type": "req",
            "id": f"connect-{device_id}",
            "method": "connect",
            "params": {
                "auth": {"token": "gateway-token-for-tests"},
                "device": {"id": device_id},
            },
        }
    )
    assert (await ws.receive_json())["ok"] is True
    return session, ws


async def send_chat(ws, *, rid: str, message: str, session_key: str = "main") -> None:
    await ws.send_json(chat_frame(rid=rid, message=message, session_key=session_key))


def chat_frame(*, rid: str, message: str, session_key: str = "main") -> dict:
    return {
        "type": "req",
        "id": rid,
        "method": "chat.send",
        "params": {
            "message": message,
            "sessionKey": session_key,
            "idempotencyKey": f"run-{rid}",
        },
    }


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "100.64.0.1", "192.168.1.20", "203.0.113.10"],
)
def test_config_allows_loopback_and_concrete_bind_hosts(tmp_path, host):
    config = R1HermesConfig(
        gateway_token="gateway-token-for-tests",
        state_dir=tmp_path,
        host=host,
    )

    assert config.host == host


@pytest.mark.parametrize("host", [WILDCARD_IPV4, "::", "::0", "0", "::ffff:0.0.0.0", ""])
def test_config_rejects_wildcard_bind_hosts_without_explicit_opt_in(tmp_path, host):
    with pytest.raises(ValueError, match="Refusing wildcard bind host"):
        R1HermesConfig(
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            host=host,
        )


@pytest.mark.parametrize("host", [WILDCARD_IPV4, "::"])
def test_config_allows_wildcard_bind_hosts_with_explicit_opt_in(tmp_path, host):
    config = R1HermesConfig(
        gateway_token="gateway-token-for-tests",
        state_dir=tmp_path,
        host=host,
        allow_public_bind=True,
    )

    assert config.host == host
    assert config.allow_public_bind is True


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
async def test_wrong_token_retries_are_rate_limited_by_peer_before_auth(
    unauth_limited_adapter,
):
    _adapter, sink, base_url = unauth_limited_adapter
    for attempt in range(2):
        session, ws = await ws_connect(base_url)
        try:
            bad_token = f"wrong-token-{attempt}"
            await ws.send_json(
                {
                    "type": "req",
                    "id": f"connect-{attempt}",
                    "method": "connect",
                    "params": {"auth": {"token": bad_token}, "device": {"id": "r1-test"}},
                }
            )
            msg = await ws.receive_json()
            serialized = json.dumps(msg)
            assert msg["ok"] is False
            assert msg["error"]["code"] == "UNAUTHORIZED"
            assert bad_token not in serialized
            close = await ws.receive()
            assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        finally:
            await session.close()

    session = ClientSession()
    try:
        ws = await session.ws_connect(base_url.replace("http", "ws") + "/")
        msg = await ws.receive()
        assert msg.type == WSMsgType.TEXT
        frame = json.loads(msg.data)
        serialized = json.dumps(frame)
        assert frame["ok"] is False
        assert frame["error"]["code"] == "RATE_LIMITED"
        assert "wrong-token" not in serialized
        close = await ws.receive()
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        assert ws.close_code == 1008
        assert sink.messages == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_concurrent_unauthenticated_connections_are_limited_by_peer(
    unauth_limited_adapter,
):
    _adapter, sink, base_url = unauth_limited_adapter
    first_session, first_ws = await ws_connect(base_url)
    second_session = ClientSession()
    try:
        second_ws = await second_session.ws_connect(base_url.replace("http", "ws") + "/")
        msg = await second_ws.receive()
        assert msg.type == WSMsgType.TEXT
        frame = json.loads(msg.data)
        assert frame["ok"] is False
        assert frame["error"]["code"] == "RATE_LIMITED"
        close = await second_ws.receive()
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        assert second_ws.close_code == 1008
        assert sink.messages == []
    finally:
        await first_ws.close()
        await first_session.close()
        await second_session.close()


@pytest.mark.asyncio
async def test_repeated_malformed_unauthenticated_frames_are_rate_limited(
    unauth_limited_adapter,
):
    _adapter, sink, base_url = unauth_limited_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_str("{not-json")
        first = await ws.receive_json()
        assert first["ok"] is False
        assert first["error"]["code"] == "BAD_JSON"

        await ws.send_str(json.dumps(["not", "an", "object"]))
        second = await ws.receive_json()
        assert second["ok"] is False
        assert second["error"]["code"] == "BAD_REQUEST"

        await ws.send_json(
            {
                "type": "req",
                "id": "connect-after-malformed",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-after-malformed"},
                },
            }
        )
        limited = await ws.receive_json()
        serialized = json.dumps(limited)
        assert limited["ok"] is False
        assert limited["error"]["code"] == "RATE_LIMITED"
        assert "gateway-token-for-tests" not in serialized
        close = await ws.receive()
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        assert ws.close_code == 1008
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
    ("fixture_name", "expected_device_id", "expected_ack_events"),
    [
        ("connect_official_helper.json", "r1-official-helper", []),
        (
            "gateway_connect_community_shim.json",
            "r1-community-shim",
            ["connect.ok", "node.pair.approved"],
        ),
    ],
)
async def test_connect_frame_variants_authenticate_and_allow_chat_send(
    running_adapter,
    fixture_name,
    expected_device_id,
    expected_ack_events,
):
    _adapter, sink, base_url = running_adapter
    frame = fixture_with_gateway_token(load_fixture(fixture_name))
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(frame)

        hello = await ws.receive_json()
        assert hello["ok"] is True
        device_token = hello["payload"]["auth"]["deviceToken"]
        assert device_token

        for expected_event in expected_ack_events:
            ack_event = await ws.receive_json()
            serialized_event = json.dumps(ack_event)
            assert ack_event["type"] == "event"
            assert ack_event["event"] == expected_event
            assert ack_event["payload"]["deviceId"] == expected_device_id
            assert device_token not in serialized_event
            assert "gateway-token-for-tests" not in serialized_event

        await ws.send_json(
            {
                "type": "req",
                "id": "chat-variant-1",
                "method": "chat.send",
                "params": {
                    "message": "hello from variant",
                    "sessionKey": "variant-session",
                    "idempotencyKey": "run-variant-1",
                },
            }
        )

        chat_ack = await ws.receive_json()
        started = await ws.receive_json()
        final = await ws.receive_json()
        serialized_chat_frames = json.dumps([chat_ack, started, final])

        assert chat_ack["ok"] is True
        assert started["payload"]["state"] == "started"
        assert final["payload"]["state"] == "final"
        assert final["payload"]["message"]["content"][0]["text"] == "echo: hello from variant"
        assert device_token not in serialized_chat_frames
        assert "gateway-token-for-tests" not in serialized_chat_frames
        assert sink.messages[-1] == {
            "text": "hello from variant",
            "device_id": expected_device_id,
            "session_key": "variant-session",
        }
    finally:
        await ws.close()
        await session.close()


@pytest.mark.asyncio
async def test_gateway_connect_rejects_bad_token_without_ack_events_or_agent_run(running_adapter):
    _adapter, sink, base_url = running_adapter
    frame = load_fixture("gateway_connect_community_shim.json")
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(frame)

        msg = await ws.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in serialized

        close = await ws.receive()
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}
        assert sink.messages == []
    finally:
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
async def test_revoked_device_token_cannot_reconnect_or_run_agent(running_adapter):
    adapter, sink, base_url = running_adapter
    session, ws = await ws_connect(base_url)
    try:
        await ws.send_json(
            {
                "type": "req",
                "id": "connect-1",
                "method": "connect",
                "params": {
                    "auth": {"token": "gateway-token-for-tests"},
                    "device": {"id": "r1-revoked"},
                },
            }
        )
        hello = await ws.receive_json()
        device_token = hello["payload"]["auth"]["deviceToken"]
    finally:
        await ws.close()
        await session.close()

    assert adapter.state.revoke("r1-revoked") is True

    session2, ws2 = await ws_connect(base_url)
    try:
        await ws2.send_json(
            {
                "type": "req",
                "id": "connect-2",
                "method": "connect",
                "params": {"auth": {"token": device_token}, "device": {"id": "r1-revoked"}},
            }
        )
        msg = await ws2.receive_json()
        serialized = json.dumps(msg)
        assert msg["ok"] is False
        assert msg["error"]["code"] == "UNAUTHORIZED"
        assert device_token not in serialized
        assert sink.messages == []
    finally:
        await session2.close()


@pytest.mark.asyncio
async def test_expired_device_token_cannot_reconnect_or_run_agent(
    monkeypatch,
    unused_tcp_port,
    tmp_path,
):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    sink = FakeHermesSink()
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            device_token_max_age_seconds=1,
            device_token_idle_timeout_seconds=0,
        ),
        message_handler=sink,
    )
    await adapter.start()
    try:
        base_url = f"http://127.0.0.1:{port}"
        session, ws = await ws_connect(base_url)
        try:
            await ws.send_json(
                {
                    "type": "req",
                    "id": "connect-1",
                    "method": "connect",
                    "params": {
                        "auth": {"token": "gateway-token-for-tests"},
                        "device": {"id": "r1-expired"},
                    },
                }
            )
            hello = await ws.receive_json()
            device_token = hello["payload"]["auth"]["deviceToken"]
        finally:
            await ws.close()
            await session.close()

        now_ms = 1_002_000

        session2, ws2 = await ws_connect(base_url)
        try:
            await ws2.send_json(
                {
                    "type": "req",
                    "id": "connect-2",
                    "method": "connect",
                    "params": {"auth": {"token": device_token}, "device": {"id": "r1-expired"}},
                }
            )
            msg = await ws2.receive_json()
            serialized = json.dumps(msg)
            assert msg["ok"] is False
            assert msg["error"]["code"] == "UNAUTHORIZED"
            assert device_token not in serialized
            assert sink.messages == []
        finally:
            await session2.close()
    finally:
        await adapter.stop()


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


@pytest.mark.asyncio
async def test_global_concurrency_rejects_excess_runs_across_devices_without_running_handler(
    unused_tcp_port,
    tmp_path,
):
    sink = BlockingHermesSink()
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
            per_device_concurrency=1,
            global_concurrency=2,
            rate_limit_messages=10,
        ),
        message_handler=sink,
    )
    await adapter.start()
    connections = []
    try:
        base_url = f"http://127.0.0.1:{port}"
        for device_id in ("r1-global-a", "r1-global-b", "r1-global-c"):
            connections.append(await authenticated_ws(base_url, device_id=device_id))

        for index, (_session, ws) in enumerate(connections[:2]):
            await send_chat(ws, rid=f"chat-{index}", message=f"hello {index}")
            ack = await ws.receive_json()
            started = await ws.receive_json()
            assert ack["ok"] is True
            assert started["payload"]["state"] == "started"

        await sink.wait_for_calls(2)

        await send_chat(connections[2][1], rid="chat-over", message="should not start")
        busy = await connections[2][1].receive_json()
        serialized = json.dumps(busy)

        assert busy["ok"] is False
        assert busy["error"] == {"code": "BUSY", "message": "gateway is busy"}
        assert len(sink.messages) == 2
        assert "should not start" not in serialized
        assert "gateway-token-for-tests" not in serialized

        sink.release.set()
        for _session, ws in connections[:2]:
            final = await ws.receive_json()
            assert final["payload"]["state"] == "final"
    finally:
        sink.release.set()
        for session, ws in connections:
            await ws.close()
            await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_per_device_concurrency_still_applies_when_global_capacity_remains(
    unused_tcp_port,
    tmp_path,
):
    sink = BlockingHermesSink()
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
            per_device_concurrency=1,
            global_concurrency=3,
            rate_limit_messages=10,
        ),
        message_handler=sink,
    )
    await adapter.start()
    sessions = []
    try:
        base_url = f"http://127.0.0.1:{port}"
        first_session, first_ws = await authenticated_ws(base_url, device_id="r1-one-device")
        sessions.append((first_session, first_ws))
        second_session, second_ws = await authenticated_ws(base_url, device_id="r1-one-device")
        sessions.append((second_session, second_ws))

        await send_chat(first_ws, rid="chat-active", message="active")
        assert (await first_ws.receive_json())["ok"] is True
        assert (await first_ws.receive_json())["payload"]["state"] == "started"
        await sink.wait_for_calls(1)

        await send_chat(second_ws, rid="chat-same-device", message="same device over limit")
        busy = await second_ws.receive_json()

        assert busy["ok"] is False
        assert busy["error"] == {"code": "BUSY", "message": "gateway is busy"}
        assert sink.messages == [
            {"text": "active", "device_id": "r1-one-device", "session_key": "main"}
        ]

        sink.release.set()
        final = await first_ws.receive_json()
        assert final["payload"]["state"] == "final"
    finally:
        sink.release.set()
        for session, ws in sessions:
            await ws.close()
            await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_global_inflight_counter_is_released_after_timeout_error(
    unused_tcp_port,
    tmp_path,
):
    sink = RaisingHermesSink(TimeoutError("DUMMY_SECRET_TOKEN_DO_NOT_USE timeout details"))
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            max_message_chars=128,
            per_device_concurrency=1,
            global_concurrency=1,
            rate_limit_messages=10,
        ),
        message_handler=sink,
    )
    await adapter.start()
    sessions = []
    try:
        base_url = f"http://127.0.0.1:{port}"
        first_session, first_ws = await authenticated_ws(base_url, device_id="r1-timeout-a")
        sessions.append((first_session, first_ws))
        await send_chat(first_ws, rid="chat-timeout", message="trigger timeout")
        assert (await first_ws.receive_json())["ok"] is True
        assert (await first_ws.receive_json())["payload"]["state"] == "started"
        timeout_event = await first_ws.receive_json()
        assert timeout_event["payload"]["state"] == "error"
        assert timeout_event["payload"]["error"] == {
            "code": "CHAT_RUN_TIMEOUT",
            "message": "chat run timed out",
        }

        adapter.message_handler = FakeHermesSink()
        second_session, second_ws = await authenticated_ws(base_url, device_id="r1-timeout-b")
        sessions.append((second_session, second_ws))
        await send_chat(second_ws, rid="chat-after-timeout", message="after timeout")
        assert (await second_ws.receive_json())["ok"] is True
        assert (await second_ws.receive_json())["payload"]["state"] == "started"
        final = await second_ws.receive_json()

        assert final["payload"]["state"] == "final"
        assert final["payload"]["message"]["content"][0]["text"] == "echo: after timeout"
        assert len(sink.messages) == 1
    finally:
        for session, ws in sessions:
            await ws.close()
            await session.close()
        await adapter.stop()


@pytest.mark.asyncio
async def test_inflight_counters_are_released_when_handler_task_is_cancelled(tmp_path):
    sink = BlockingHermesSink()
    adapter = R1HermesAdapter(
        R1HermesConfig(
            gateway_token="gateway-token-for-tests",
            state_dir=tmp_path,
            per_device_concurrency=1,
            global_concurrency=1,
            rate_limit_messages=10,
        ),
        message_handler=sink,
    )
    first_ws = FakeWebSocket()
    task = asyncio.create_task(
        adapter._handle_chat_send(
            first_ws,
            "chat-cancelled",
            chat_frame(rid="chat-cancelled", message="will be cancelled"),
            "r1-cancelled",
        )
    )
    await sink.wait_for_calls(1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    adapter.message_handler = FakeHermesSink()
    second_ws = FakeWebSocket()
    await adapter._handle_chat_send(
        second_ws,
        "chat-after-cancel",
        chat_frame(rid="chat-after-cancel", message="after cancel"),
        "r1-after-cancel",
    )

    assert second_ws.frames[0]["ok"] is True
    assert second_ws.frames[1]["payload"]["state"] == "started"
    assert second_ws.frames[2]["payload"]["state"] == "final"
    assert second_ws.frames[2]["payload"]["message"]["content"][0]["text"] == (
        "echo: after cancel"
    )


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


def test_config_from_env_reads_global_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-token-for-tests")
    monkeypatch.setenv("R1_HERMES_GLOBAL_CONCURRENCY", "4")
    monkeypatch.setenv("R1_HERMES_PER_DEVICE_CONCURRENCY", "2")

    config = R1HermesConfig.from_env(state_dir=tmp_path)

    assert config.global_concurrency == 4
    assert config.per_device_concurrency == 2


def test_config_rejects_invalid_concurrency(tmp_path):
    with pytest.raises(ValueError, match="global_concurrency"):
        R1HermesAdapter(
            R1HermesConfig(
                gateway_token="gateway-token-for-tests",
                state_dir=tmp_path,
                global_concurrency=0,
            ),
            message_handler=FakeHermesSink(),
        )


def test_device_state_permissions_are_owner_only(tmp_path):
    state = DeviceState(tmp_path / "state")
    token = state.issue_device_token("r1-test")
    assert token
    assert stat.S_IMODE(state.state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.path.stat().st_mode) == 0o600


def test_device_state_uses_keyed_digest_for_new_records(tmp_path):
    state = DeviceState(tmp_path / "state")
    token = state.issue_device_token("r1-test")

    token_hash = state.devices["r1-test"].token_hash
    raw_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()

    assert token_hash.startswith("hmac-sha256:v1:")
    assert token_hash != raw_sha256
    assert token not in state.path.read_text()
    assert raw_sha256 not in state.path.read_text()


def test_device_state_upgrades_legacy_sha256_digest_after_valid_auth(monkeypatch, tmp_path):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    token = "legacy-device-token-for-tests"
    legacy_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    state_path = state_dir / "devices.json"
    state_path.write_text(
        json.dumps(
            {
                "devices": {
                    "r1-legacy": {
                        "device_id": "r1-legacy",
                        "token_hash": legacy_hash,
                        "display_name": "Rabbit R1",
                        "created_at_ms": 1000,
                        "last_seen_at_ms": 1000,
                    }
                }
            }
        )
    )
    state_path.chmod(0o600)

    state = DeviceState(state_dir)

    assert state.verify_device_token("r1-legacy", token) is True

    upgraded_hash = state.devices["r1-legacy"].token_hash
    saved_hash = json.loads(state_path.read_text())["devices"]["r1-legacy"]["token_hash"]
    assert upgraded_hash.startswith("hmac-sha256:v1:")
    assert saved_hash == upgraded_hash
    assert upgraded_hash != legacy_hash
    assert stat.S_IMODE(state.key_path.stat().st_mode) == 0o600


def test_device_state_does_not_upgrade_legacy_sha256_digest_after_invalid_auth(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    legacy_hash = hashlib.sha256(b"correct-device-token").hexdigest()
    state_path = state_dir / "devices.json"
    state_path.write_text(
        json.dumps(
            {
                "devices": {
                    "r1-legacy": {
                        "device_id": "r1-legacy",
                        "token_hash": legacy_hash,
                    }
                }
            }
        )
    )
    state_path.chmod(0o600)

    state = DeviceState(state_dir)

    assert state.verify_device_token("r1-legacy", "wrong-device-token") is False
    assert state.devices["r1-legacy"].token_hash == legacy_hash
    assert json.loads(state_path.read_text())["devices"]["r1-legacy"]["token_hash"] == legacy_hash


def test_device_state_rejects_symlinked_digest_key(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    target = tmp_path / "external-key"
    target.write_text("00" * 32)
    (state_dir / "device-token-hmac.key").symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        DeviceState(state_dir)


def test_device_state_accepts_fresh_token_and_updates_last_seen(monkeypatch, tmp_path):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = DeviceState(
        tmp_path / "state",
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=30,
    )
    token = state.issue_device_token("r1-fresh")

    now_ms = 1_020_000

    assert state.verify_device_token("r1-fresh", token) is True
    assert state.devices["r1-fresh"].last_seen_at_ms == 1_020_000


def test_device_state_rejects_expired_token_with_hash_still_present(monkeypatch, tmp_path):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = DeviceState(
        tmp_path / "state",
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=0,
    )
    token = state.issue_device_token("r1-old")
    assert "r1-old" in state.devices

    now_ms = 1_061_000

    assert state.verify_device_token("r1-old", token) is False
    assert "r1-old" in state.devices


def test_device_state_rejects_idle_expired_token(monkeypatch, tmp_path):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = DeviceState(
        tmp_path / "state",
        device_token_max_age_seconds=0,
        device_token_idle_timeout_seconds=30,
    )
    token = state.issue_device_token("r1-idle")

    now_ms = 1_031_000

    assert state.verify_device_token("r1-idle", token) is False


def test_device_state_backfills_existing_records_missing_expiry_fields(monkeypatch, tmp_path):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = DeviceState(tmp_path / "state")
    token = state.issue_device_token("r1-existing")
    data = json.loads(state.path.read_text())
    del data["devices"]["r1-existing"]["created_at_ms"]
    del data["devices"]["r1-existing"]["last_seen_at_ms"]
    state.path.write_text(json.dumps(data))

    now_ms = 1_050_000
    migrated = DeviceState(
        tmp_path / "state",
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=30,
    )
    record = migrated.devices["r1-existing"]

    assert record.created_at_ms == 1_050_000
    assert record.last_seen_at_ms == 1_050_000
    saved = json.loads(migrated.path.read_text())["devices"]["r1-existing"]
    assert saved["created_at_ms"] == 1_050_000
    assert saved["last_seen_at_ms"] == 1_050_000
    assert migrated.verify_device_token("r1-existing", token) is True


def test_device_state_prunes_expired_records_without_breaking_valid_devices(
    monkeypatch,
    tmp_path,
):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = DeviceState(
        tmp_path / "state",
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=0,
    )
    old_token = state.issue_device_token("r1-old")

    now_ms = 1_050_000
    fresh_token = state.issue_device_token("r1-fresh")

    now_ms = 1_061_000

    assert state.prune_expired() == 1
    assert "r1-old" not in state.devices
    assert "r1-fresh" in state.devices
    assert state.verify_device_token("r1-old", old_token) is False
    assert state.verify_device_token("r1-fresh", fresh_token) is True
