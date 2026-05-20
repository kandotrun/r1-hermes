import pytest
import pytest_asyncio
from aiohttp import web

from r1_hermes.adapter import R1HermesAdapter, R1HermesConfig
from r1_hermes.r1_client import R1ProbeClient, R1ProbeError


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
async def test_probe_client_reports_auth_failure(running_gateway):
    client = R1ProbeClient(url=running_gateway, token="wrong", device_id="r1-probe")

    with pytest.raises(R1ProbeError, match="UNAUTHORIZED"):
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
