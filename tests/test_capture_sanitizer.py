from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from r1_hermes.capture_sanitizer import (
    DUMMY_DEVICE_TOKEN,
    DUMMY_GATEWAY_TOKEN,
    SANITIZED_ASSISTANT_TEXT,
    SANITIZED_DEVICE_ID,
    SANITIZED_MESSAGE_TEXT,
    CaptureSchemaError,
    sanitize_capture,
    validate_sanitized_capture,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "r1_payloads")
RAW_GATEWAY_TOKEN = "DUMMY_PRIVATE_CAPTURE_GATEWAY_TOKEN_DO_NOT_USE"
RAW_DEVICE_TOKEN = "DUMMY_PRIVATE_CAPTURE_DEVICE_TOKEN_DO_NOT_USE"
RAW_DEVICE_ID = "DUMMY_PRIVATE_CAPTURE_DEVICE_ID_DO_NOT_USE"
RAW_PROMPT = "DUMMY private prompt text that must not be committed"
RAW_ASSISTANT_TEXT = "DUMMY private assistant text that must not be committed"
RAW_CLIENT_NAME = "DUMMY_PRIVATE_CAPTURE_CLIENT_NAME_DO_NOT_USE"


def private_capture_frames() -> list[dict[str, object]]:
    return [
        {
            "type": "req",
            "id": "raw-connect-request-id",
            "method": "connect",
            "params": {
                "auth": {"token": RAW_GATEWAY_TOKEN},
                "device": {"id": RAW_DEVICE_ID, "serialNumber": "raw-serial-987"},
                "client": {"displayName": RAW_CLIENT_NAME},
            },
        },
        {
            "type": "res",
            "id": "raw-connect-request-id",
            "ok": True,
            "payload": {
                "type": "hello-ok",
                "auth": {"deviceToken": RAW_DEVICE_TOKEN, "role": "operator"},
            },
        },
        {
            "type": "event",
            "event": "node.pair.approved",
            "payload": {"ok": True, "deviceId": RAW_DEVICE_ID, "ts": 1780000000123},
        },
        {
            "type": "req",
            "id": "raw-chat-request-id",
            "method": "chat.send",
            "payload": {
                "message": {"content": [{"type": "input_text", "text": RAW_PROMPT}]},
                "session": {"id": "raw-conversation-id"},
                "runId": "raw-run-id",
                "auth": {"deviceToken": RAW_DEVICE_TOKEN},
            },
        },
        {
            "type": "event",
            "event": "chat",
            "payload": {
                "runId": "raw-run-id",
                "sessionKey": "raw-conversation-id",
                "seq": 2,
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": RAW_ASSISTANT_TEXT}],
                    "timestamp": 1780000000456,
                },
            },
        },
    ]


def test_sanitize_capture_replaces_tokens_device_ids_and_prompt_text() -> None:
    sanitized = sanitize_capture(private_capture_frames())

    validate_sanitized_capture(
        sanitized,
        forbidden_values=(RAW_GATEWAY_TOKEN, RAW_DEVICE_TOKEN, RAW_DEVICE_ID, RAW_PROMPT),
    )
    serialized = json.dumps(sanitized, sort_keys=True)
    assert RAW_GATEWAY_TOKEN not in serialized
    assert RAW_DEVICE_TOKEN not in serialized
    assert RAW_DEVICE_ID not in serialized
    assert RAW_PROMPT not in serialized
    assert RAW_ASSISTANT_TEXT not in serialized
    assert RAW_CLIENT_NAME not in serialized
    assert "raw-" not in serialized
    assert DUMMY_GATEWAY_TOKEN in serialized
    assert DUMMY_DEVICE_TOKEN in serialized
    assert SANITIZED_DEVICE_ID in serialized
    assert SANITIZED_MESSAGE_TEXT in serialized
    assert SANITIZED_ASSISTANT_TEXT in serialized


def test_validate_sanitized_capture_rejects_raw_auth_values() -> None:
    with pytest.raises(CaptureSchemaError, match="auth token must use a public dummy value"):
        validate_sanitized_capture(private_capture_frames()[0])


def test_validate_sanitized_capture_accepts_unsupported_media_fixture_shape() -> None:
    validate_sanitized_capture(
        {
            "type": "req",
            "id": "media-frame-001",
            "method": "chat.send",
            "params": {
                "message": {
                    "content": [
                        {"type": "input_text", "text": SANITIZED_MESSAGE_TEXT},
                        {
                            "type": "input_audio",
                            "mediaType": "audio/wav",
                            "data": "DUMMY_BINARY_DATA_OMITTED",
                        },
                    ]
                },
                "sessionKey": "media-main",
                "requestId": "media-run-001",
                "auth": {"deviceToken": DUMMY_DEVICE_TOKEN},
            },
        }
    )


@pytest.mark.parametrize(
    "fixture_name",
    sorted(name for name in os.listdir(FIXTURE_DIR) if name.endswith(".json")),
)
def test_committed_r1_payload_fixtures_match_public_schema(fixture_name: str) -> None:
    with open(os.path.join(FIXTURE_DIR, fixture_name)) as handle:
        validate_sanitized_capture(json.load(handle))


def test_capture_sanitizer_module_command_writes_valid_public_fixture(tmp_path) -> None:
    private_path = tmp_path / "private-capture.json"
    output_path = tmp_path / "public-fixture.json"
    private_path.write_text(json.dumps(private_capture_frames()))
    env = {**os.environ, "PYTHONPATH": "src"}

    result = subprocess.run(  # noqa: S603 - fixed Python module invocation, no shell
        [
            sys.executable,
            "-m",
            "r1_hermes.capture_sanitizer",
            "--input",
            str(private_path),
            "--output",
            str(output_path),
            "--forbid",
            RAW_GATEWAY_TOKEN,
            "--forbid",
            RAW_DEVICE_TOKEN,
            "--forbid",
            RAW_DEVICE_ID,
            "--forbid",
            RAW_PROMPT,
        ],
        check=False,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote sanitized fixture" in result.stdout
    assert not result.stderr
    sanitized = json.loads(output_path.read_text())
    validate_sanitized_capture(
        sanitized,
        forbidden_values=(RAW_GATEWAY_TOKEN, RAW_DEVICE_TOKEN, RAW_DEVICE_ID, RAW_PROMPT),
    )
