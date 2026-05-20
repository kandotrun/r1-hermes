import json
from pathlib import Path

import pytest
import pytest_asyncio

from r1_hermes.adapter import R1HermesAdapter, R1HermesConfig
from r1_hermes.qr import build_pairing_payload

from .replay_helpers import FixtureReplayFlow, replay_fixture_flow

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r1_payloads"
DUMMY_GATEWAY_TOKEN = "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"
DUMMY_DEVICE_TOKEN = "DUMMY_DEVICE_TOKEN_DO_NOT_USE"
TEST_GATEWAY_TOKEN = "gateway-token-for-fixture-replay"


class ReplaySink:
    def __init__(self):
        self.messages = []

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        self.messages.append({"text": text, "device_id": device_id, "session_key": session_key})
        return f"fixture echo: {device_id}/{session_key}: {text}"


@pytest_asyncio.fixture
async def replay_gateway(unused_tcp_port, tmp_path):
    sink = ReplaySink()
    adapter = R1HermesAdapter(
        R1HermesConfig(
            host="127.0.0.1",
            port=unused_tcp_port,
            gateway_token=TEST_GATEWAY_TOKEN,
            state_dir=tmp_path,
            max_message_chars=512,
        ),
        message_handler=sink,
    )
    await adapter.start()
    try:
        yield f"ws://127.0.0.1:{unused_tcp_port}/", sink
    finally:
        await adapter.stop()


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def test_official_helper_qr_payload_fixture_matches_generated_shape():
    fixture = load_fixture("official_helper_qr_payload.json")
    generated = json.loads(
        build_pairing_payload(
            hosts=fixture["ips"],
            port=fixture["port"],
            token=fixture["token"],
            protocol=fixture["protocol"],
        )
    )

    assert list(fixture) == ["type", "version", "ips", "port", "token", "protocol"]
    assert generated == fixture
    assert generated["type"] == "clawdbot-gateway"
    assert generated["version"] == 1
    assert generated["token"] == DUMMY_GATEWAY_TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow",
    [
        FixtureReplayFlow(
            connect_fixture="connect_official_helper.json",
            chat_fixture="chat_send_payload_aliases.json",
            history_fixture="chat_history_payload_aliases.json",
            expected_device_id="r1-official-helper",
            expected_message="hello Hermes from sanitized capture",
            expected_session_key="capture-main",
            expected_run_id="sample-run-001",
            expected_ack_events=(),
        ),
        FixtureReplayFlow(
            connect_fixture="gateway_connect_community_shim.json",
            chat_fixture="community_shim_chat_message_object.json",
            expected_device_id="r1-community-shim",
            expected_message="hello Hermes from community shim fixture",
            expected_session_key="community-main",
            expected_run_id="community-run-001",
            expected_ack_events=("connect.ok", "node.pair.approved"),
        ),
        FixtureReplayFlow(
            connect_fixture="openclaw_ui_connect_nested_auth.json",
            chat_fixture="openclaw_ui_chat_content_parts.json",
            expected_device_id="r1-openclaw-ui",
            expected_message="hello Hermes from OpenClaw UI fixture",
            expected_session_key="openclaw-ui-main",
            expected_run_id="openclaw-ui-run-001",
            expected_ack_events=("connect.ok", "node.pair.approved"),
        ),
    ],
)
async def test_sanitized_fixture_flows_replay_without_secret_leakage(replay_gateway, flow):
    url, sink = replay_gateway

    result = await replay_fixture_flow(
        url=url,
        fixture_dir=FIXTURE_DIR,
        flow=flow,
        gateway_token=TEST_GATEWAY_TOKEN,
    )

    assert result.response_text == (
        f"fixture echo: {flow.expected_device_id}/{flow.expected_session_key}: "
        f"{flow.expected_message}"
    )
    assert sink.messages[-1] == {
        "text": flow.expected_message,
        "device_id": flow.expected_device_id,
        "session_key": flow.expected_session_key,
    }
    assert result.run_id == flow.expected_run_id
    assert DUMMY_GATEWAY_TOKEN not in repr(result)
    assert DUMMY_DEVICE_TOKEN not in repr(result)
    assert result.device_token not in repr(result)
    assert TEST_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_DEVICE_TOKEN not in result.serialized_frames
    assert "[REDACTED]" in result.serialized_frames
    assert result.device_token not in result.serialized_chat_frames
