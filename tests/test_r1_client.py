import pytest
import pytest_asyncio
from aiohttp import web

from r1_hermes.adapter import R1HermesAdapter, R1HermesConfig
from r1_hermes.r1_client import R1ProbeClient, R1ProbeError, redact_frame_secrets

DUMMY_GATEWAY_TOKEN = "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"
DUMMY_DEVICE_TOKEN = "DUMMY_DEVICE_TOKEN_DO_NOT_USE"
ISSUED_DEVICE_TOKEN = "issued-device-token-for-redaction"


class EchoSink:
    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        return f"echo: {device_id}/{session_key}: {text}"


class RaisingSink:
    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        raise RuntimeError("DUMMY_SECRET_TOKEN_DO_NOT_USE failure details")


@pytest_asyncio.fixture
async def running_gateway(unused_tcp_port, tmp_path):
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="probe-token",
            state_dir=tmp_path,
            max_message_chars=512,
        ),
        message_handler=EchoSink(),
    )
    await adapter.start()
    try:
        yield f"ws://127.0.0.1:{port}/"
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_probe_client_completes_connect_and_chat_flow(running_gateway):
    client = R1ProbeClient(url=running_gateway, token="probe-token", device_id="r1-probe")

    result = await client.send_message("hello Hermes", session_key="main")

    assert result.connected is True
    assert result.device_token
    assert result.run_id
    assert result.raw_event["payload"]["state"] == "final"
    assert result.response_text == "echo: r1-probe/main: hello Hermes"
    assert result.device_token not in repr(result)
    assert "deviceToken" not in repr(result)


@pytest.mark.asyncio
async def test_probe_client_can_reuse_device_token(running_gateway):
    first = R1ProbeClient(url=running_gateway, token="probe-token", device_id="r1-probe")
    first_result = await first.send_message("first")

    second = R1ProbeClient(
        url=running_gateway, token=first_result.device_token, device_id="r1-probe"
    )
    second_result = await second.send_message("second", session_key="followup")

    assert second_result.response_text == "echo: r1-probe/followup: second"
    assert second_result.device_token == first_result.device_token


@pytest.mark.asyncio
async def test_probe_client_can_use_gateway_connect_variant(running_gateway):
    client = R1ProbeClient(
        url=running_gateway,
        token="probe-token",
        device_id="r1-gateway-probe",
        connect_method="gateway.connect",
    )

    result = await client.send_message("hello via gateway.connect", session_key="variant")

    assert result.connected is True
    assert result.device_token
    assert result.response_text == "echo: r1-gateway-probe/variant: hello via gateway.connect"


@pytest.mark.asyncio
async def test_probe_client_reports_auth_failure(running_gateway):
    client = R1ProbeClient(url=running_gateway, token="wrong", device_id="r1-probe")

    with pytest.raises(R1ProbeError, match="UNAUTHORIZED"):
        await client.send_message("hello")


@pytest.mark.asyncio
async def test_probe_client_safe_frame_dump_redacts_auth_tokens(unused_tcp_port):
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(
            {
                "type": "event",
                "event": "connect.challenge",
                "payload": {"nonce": "n-1"},
            }
        )
        connect = await ws.receive_json()
        assert connect["params"]["auth"]["token"] == DUMMY_GATEWAY_TOKEN
        await ws.send_json(
            {
                "type": "res",
                "id": connect["id"],
                "ok": True,
                "payload": {
                    "auth": {"deviceToken": ISSUED_DEVICE_TOKEN},
                    "debug": f"issued {ISSUED_DEVICE_TOKEN}",
                },
            }
        )
        chat = await ws.receive_json()
        await ws.send_json(
            {
                "type": "res",
                "id": chat["id"],
                "ok": True,
                "payload": {"runId": "dump-run", "status": "started"},
            }
        )
        await ws.send_json(
            {
                "type": "event",
                "event": "chat",
                "payload": {
                    "runId": "dump-run",
                    "state": "final",
                    "message": {
                        "content": [{"type": "text", "text": "dump ok"}],
                    },
                },
            }
        )
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    frames = []
    try:
        client = R1ProbeClient(
            url=f"ws://127.0.0.1:{unused_tcp_port}/",
            token=DUMMY_GATEWAY_TOKEN,
            device_id="r1-dump",
            timeout_seconds=1,
            dump_frames=True,
            frame_sink=frames.append,
        )
        result = await client.send_message("hello dump")
    finally:
        await runner.cleanup()

    serialized = str(frames)
    assert result.response_text == "dump ok"
    assert frames
    assert DUMMY_GATEWAY_TOKEN not in serialized
    assert DUMMY_DEVICE_TOKEN not in serialized
    assert ISSUED_DEVICE_TOKEN not in serialized
    assert "r1-dump" not in serialized
    assert "hello dump" not in serialized
    assert "dump ok" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_probe_client_safe_frame_dump_captures_failed_connect_without_secret_leak(
    running_gateway,
):
    frames = []
    client = R1ProbeClient(
        url=running_gateway,
        token=DUMMY_GATEWAY_TOKEN,
        device_id="r1-dump-failure",
        dump_frames=True,
        frame_sink=frames.append,
    )

    with pytest.raises(R1ProbeError) as exc_info:
        await client.send_message("hello")

    serialized = str(frames)
    assert "UNAUTHORIZED" in str(exc_info.value)
    assert DUMMY_GATEWAY_TOKEN not in str(exc_info.value)
    assert DUMMY_GATEWAY_TOKEN not in serialized
    assert "r1-dump-failure" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_probe_client_redacts_echoed_auth_token_from_dump_and_exception(unused_tcp_port):
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "event", "event": "connect.challenge", "payload": {}})
        connect = await ws.receive_json()
        await ws.send_json(
            {
                "type": "res",
                "id": connect["id"],
                "ok": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": f"bad token {DUMMY_GATEWAY_TOKEN}",
                },
            }
        )
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/", ws_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    frames = []
    try:
        client = R1ProbeClient(
            url=f"ws://127.0.0.1:{unused_tcp_port}/",
            token=DUMMY_GATEWAY_TOKEN,
            device_id="r1-echo-token",
            dump_frames=True,
            frame_sink=frames.append,
        )
        with pytest.raises(R1ProbeError) as exc_info:
            await client.send_message("hello")
    finally:
        await runner.cleanup()

    serialized = str(frames)
    assert "UNAUTHORIZED" in str(exc_info.value)
    assert DUMMY_GATEWAY_TOKEN not in str(exc_info.value)
    assert DUMMY_GATEWAY_TOKEN not in serialized
    assert "[REDACTED]" in serialized


def test_redact_frame_secrets_removes_known_values_from_nested_strings():
    redacted = redact_frame_secrets(
        {
            "payload": {"auth": {"deviceToken": DUMMY_DEVICE_TOKEN}},
            "params": {"device": {"id": "r1-private-device"}},
            "message": {"content": [{"type": "text", "text": "private prompt"}]},
            "error": {"message": f"bad {DUMMY_GATEWAY_TOKEN} and {DUMMY_DEVICE_TOKEN}"},
        },
        secret_values=(DUMMY_GATEWAY_TOKEN, DUMMY_DEVICE_TOKEN),
    )

    serialized = str(redacted)
    assert DUMMY_GATEWAY_TOKEN not in serialized
    assert DUMMY_DEVICE_TOKEN not in serialized
    assert "r1-private-device" not in serialized
    assert "private prompt" not in serialized
    assert serialized.count("[REDACTED]") >= 2


@pytest.mark.asyncio
async def test_probe_client_rejects_unsupported_connect_method_before_network(running_gateway):
    client = R1ProbeClient(
        url=running_gateway,
        token="probe-token",
        device_id="r1-probe",
        connect_method="node.pair.approved",
    )

    with pytest.raises(R1ProbeError, match="unsupported connect method"):
        await client.send_message("hello")


@pytest.mark.asyncio
async def test_probe_client_fails_on_chat_error_event(unused_tcp_port, tmp_path):
    port = unused_tcp_port
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=port,
            gateway_token="probe-token",
            state_dir=tmp_path,
            max_message_chars=512,
        ),
        message_handler=RaisingSink(),
    )
    await adapter.start()
    try:
        client = R1ProbeClient(url=f"ws://127.0.0.1:{port}/", token="probe-token")
        with pytest.raises(R1ProbeError) as excinfo:
            await client.send_message("hello")

        error_text = str(excinfo.value)
        assert "CHAT_RUN_FAILED: chat run failed" in error_text
        assert "DUMMY_SECRET_TOKEN_DO_NOT_USE" not in error_text
        assert "probe-token" not in error_text
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_probe_client_requires_connect_challenge(unused_tcp_port):
    async def plain_response(_request):
        return web.WebSocketResponse()

    app = web.Application()
    app.router.add_get("/", plain_response)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    try:
        client = R1ProbeClient(
            url=f"ws://127.0.0.1:{unused_tcp_port}/",
            token="probe-token",
            device_id="r1-probe",
            timeout_seconds=0.2,
        )
        with pytest.raises(R1ProbeError, match="connect.challenge"):
            await client.send_message("hello")
    finally:
        await runner.cleanup()
