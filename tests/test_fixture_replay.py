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

    async def __call__(self, text: str, *, device_id: str, session_key: str, attachments=()) -> str:
        self.messages.append(
            {
                "text": text,
                "device_id": device_id,
                "session_key": session_key,
                "attachments": attachments,
            }
        )
        return f"fixture echo: {device_id}/{session_key}: {text} ({len(attachments)} attachments)"


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
        f"{flow.expected_message} (0 attachments)"
    )
    assert sink.messages[-1] == {
        "text": flow.expected_message,
        "device_id": flow.expected_device_id,
        "session_key": flow.expected_session_key,
        "attachments": (),
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_fixture", "dummy_media", "text_prompt"),
    [
        (
            "chat_send_mixed_text_audio_content.json",
            "DUMMY_BINARY_DATA_OMITTED",
            "please describe this audio",
        )
    ],
)
async def test_media_fixture_flows_return_unsupported_media_without_secret_leakage(
    replay_gateway,
    chat_fixture,
    dummy_media,
    text_prompt,
):
    url, sink = replay_gateway

    result = await replay_fixture_flow(
        url=url,
        fixture_dir=FIXTURE_DIR,
        flow=FixtureReplayFlow(
            connect_fixture="connect_official_helper.json",
            chat_fixture=chat_fixture,
            expected_device_id="r1-official-helper",
            expected_message=None,
            expected_session_key="media-main",
            expected_run_id="",
            expected_error_code="UNSUPPORTED_MEDIA",
            expected_error_message="unsupported media content",
        ),
        gateway_token=TEST_GATEWAY_TOKEN,
    )

    assert result.response_text is None
    assert result.run_id is None
    assert result.error_code == "UNSUPPORTED_MEDIA"
    assert result.error_message == "unsupported media content"
    assert sink.messages == []
    assert dummy_media not in repr(result)
    assert dummy_media not in result.serialized_frames
    if text_prompt is not None:
        assert text_prompt not in result.serialized_chat_frames
    assert TEST_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_DEVICE_TOKEN not in result.serialized_frames
    assert result.device_token not in result.serialized_frames


@pytest.mark.asyncio
async def test_public_image_fixture_flow_passes_media_path_and_prompt_to_hermes(
    replay_gateway,
):
    url, sink = replay_gateway

    result = await replay_fixture_flow(
        url=url,
        fixture_dir=FIXTURE_DIR,
        flow=FixtureReplayFlow(
            connect_fixture="connect_official_helper.json",
            chat_fixture="chat_send_public_image_content.json",
            expected_device_id="r1-official-helper",
            expected_message="describe the sanitized image",
            expected_session_key="media-main",
            expected_run_id="public-image-run-001",
        ),
        gateway_token=TEST_GATEWAY_TOKEN,
    )

    assert result.run_id == "public-image-run-001"
    assert len(sink.messages) == 1
    message = sink.messages[0]
    assert message["device_id"] == "r1-official-helper"
    assert message["session_key"] == "media-main"
    assert message["text"].endswith("\n\ndescribe the sanitized image")
    media_line = message["text"].splitlines()[0]
    assert media_line.startswith("MEDIA:")
    media_path = Path(media_line.removeprefix("MEDIA:"))
    assert media_path.is_absolute()
    assert media_path.exists() is False
    assert len(message["attachments"]) == 1
    attachment = message["attachments"][0]
    assert attachment.mime_type == "image/png"
    assert attachment.filename == "public-test-image.png"
    assert attachment.content_hash.startswith("sha256:")
    assert result.response_text is not None
    assert result.response_text.startswith("fixture echo: r1-official-helper/media-main: MEDIA:")
    assert result.response_text.endswith("\n\ndescribe the sanitized image (1 attachments)")
    assert TEST_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_DEVICE_TOKEN not in result.serialized_frames
    assert result.device_token not in result.serialized_frames


@pytest.mark.asyncio
async def test_camera_image_placeholder_fixture_rejects_before_agent_without_secret_leakage(
    replay_gateway,
):
    url, sink = replay_gateway

    result = await replay_fixture_flow(
        url=url,
        fixture_dir=FIXTURE_DIR,
        flow=FixtureReplayFlow(
            connect_fixture="connect_official_helper.json",
            chat_fixture="chat_send_media_only_image_content.json",
            expected_device_id="r1-official-helper",
            expected_message=None,
            expected_session_key="media-main",
            expected_run_id="",
            expected_error_code="UNSUPPORTED_MEDIA",
            expected_error_message="unsupported media content",
        ),
        gateway_token=TEST_GATEWAY_TOKEN,
    )

    assert result.response_text is None
    assert result.run_id is None
    assert result.error_code == "UNSUPPORTED_MEDIA"
    assert result.error_message == "unsupported media content"
    assert sink.messages == []
    assert "cjEtaW1hZ2U=" not in repr(result)
    assert "cjEtaW1hZ2U=" not in result.serialized_frames
    assert "data:image" not in result.serialized_frames
    assert TEST_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_DEVICE_TOKEN not in result.serialized_frames
    assert result.device_token not in result.serialized_frames


@pytest.mark.asyncio
async def test_real_device_camera_media_flow_fixture_replays_as_attachment_without_leakage(
    replay_gateway,
):
    url, sink = replay_gateway

    result = await replay_fixture_flow(
        url=url,
        fixture_dir=FIXTURE_DIR,
        flow=FixtureReplayFlow(
            connect_fixture="real_device_camera_media_flow.json",
            connect_frame_id="connect-camera-flow-001",
            chat_fixture="real_device_camera_media_flow.json",
            chat_frame_id="chat-camera-flow-001",
            expected_device_id="r1-camera-flow",
            expected_message="describe the sanitized camera image",
            expected_session_key="camera-main",
            expected_run_id="camera-run-001",
            expected_ack_events=("connect.ok", "node.pair.approved"),
        ),
        gateway_token=TEST_GATEWAY_TOKEN,
    )

    assert result.run_id == "camera-run-001"
    assert len(sink.messages) == 1
    message = sink.messages[0]
    assert message["text"].endswith("\n\ndescribe the sanitized camera image")
    media_line = message["text"].splitlines()[0]
    assert media_line.startswith("MEDIA:")
    media_path = Path(media_line.removeprefix("MEDIA:"))
    assert media_path.is_absolute()
    assert media_path.exists() is False
    assert message["device_id"] == "r1-camera-flow"
    assert message["session_key"] == "camera-main"
    assert len(message["attachments"]) == 1
    attachment = message["attachments"][0]
    assert attachment.mime_type == "image/png"
    assert attachment.filename == "r1-camera.png"
    assert attachment.content_hash.startswith("sha256:")
    assert result.response_text is not None
    assert result.response_text.startswith("fixture echo: r1-camera-flow/camera-main: MEDIA:")
    assert result.response_text.endswith("\n\ndescribe the sanitized camera image (1 attachments)")
    assert "cjEtaW1hZ2U=" not in repr(result)
    assert "cjEtaW1hZ2U=" not in result.serialized_frames
    assert "data:image" not in result.serialized_frames
    assert TEST_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_GATEWAY_TOKEN not in result.serialized_frames
    assert DUMMY_DEVICE_TOKEN not in result.serialized_frames
    assert result.device_token not in result.serialized_frames
