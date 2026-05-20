import json
from pathlib import Path

import pytest

from r1_hermes.payloads import (
    PayloadParseError,
    parse_chat_send_params,
    parse_connect_params,
    request_params,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r1_payloads"


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def test_connect_fixture_payload_aliases_are_normalized_without_repr_secret_leakage():
    frame = load_fixture("connect_payload_aliases.json")

    request = parse_connect_params(request_params(frame))

    assert request.auth_token == "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"
    assert request.device_id == "r1-sanitized-capture"
    assert request.display_name == "OpenClaw"
    assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in repr(request)


@pytest.mark.parametrize(
    ("fixture_name", "expected_device_id", "expected_display_name"),
    [
        (
            "connect_official_helper.json",
            "r1-official-helper",
            "Rabbit R1 official helper",
        ),
        (
            "gateway_connect_community_shim.json",
            "r1-community-shim",
            "OpenClaw community shim",
        ),
        (
            "openclaw_ui_connect_nested_auth.json",
            "r1-openclaw-ui",
            "OpenClaw UI",
        ),
    ],
)
def test_connect_variant_fixtures_are_normalized_without_repr_secret_leakage(
    fixture_name,
    expected_device_id,
    expected_display_name,
):
    frame = load_fixture(fixture_name)

    request = parse_connect_params(request_params(frame))

    assert request.auth_token == "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"
    assert request.device_id == expected_device_id
    assert request.display_name == expected_display_name
    assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in repr(request)


def test_chat_fixture_payload_aliases_are_normalized_and_ignores_device_token():
    frame = load_fixture("chat_send_payload_aliases.json")

    request = parse_chat_send_params(request_params(frame))

    assert request.message == "hello Hermes from sanitized capture"
    assert request.session_key == "capture-main"
    assert request.idempotency_key == "sample-run-001"
    assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in repr(request)


@pytest.mark.parametrize(
    ("fixture_name", "expected_message", "expected_session_key", "expected_run_id"),
    [
        (
            "community_shim_chat_message_object.json",
            "hello Hermes from community shim fixture",
            "community-main",
            "community-run-001",
        ),
        (
            "openclaw_ui_chat_content_parts.json",
            "hello Hermes from OpenClaw UI fixture",
            "openclaw-ui-main",
            "openclaw-ui-run-001",
        ),
    ],
)
def test_chat_variant_fixtures_are_normalized_without_repr_secret_leakage(
    fixture_name,
    expected_message,
    expected_session_key,
    expected_run_id,
):
    frame = load_fixture(fixture_name)

    request = parse_chat_send_params(request_params(frame))

    assert request.message == expected_message
    assert request.session_key == expected_session_key
    assert request.idempotency_key == expected_run_id
    assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in repr(request)


@pytest.mark.parametrize(
    ("fixture_name", "dummy_media"),
    [
        ("chat_send_mixed_text_audio_content.json", "DUMMY_BINARY_DATA_OMITTED"),
        ("chat_send_media_only_image_content.json", "DUMMY_BINARY_DATA_OMITTED"),
    ],
)
def test_chat_media_fixtures_raise_safe_unsupported_media_without_leaking_payload(
    fixture_name,
    dummy_media,
):
    frame = load_fixture(fixture_name)

    with pytest.raises(PayloadParseError) as exc_info:
        parse_chat_send_params(request_params(frame))

    assert exc_info.value.code == "UNSUPPORTED_MEDIA"
    assert str(exc_info.value) == "unsupported media content"
    assert dummy_media not in str(exc_info.value)
    assert dummy_media not in repr(exc_info.value)
    assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ({"authToken": "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"}, "device.id is required"),
        ({"deviceId": "r1-test"}, "auth token is required"),
    ],
)
def test_connect_required_fields_raise_explicit_errors(params, match):
    with pytest.raises(PayloadParseError, match=match):
        parse_connect_params(params)


@pytest.mark.parametrize(
    "params",
    [
        {"message": ""},
    ],
)
def test_chat_message_is_required(params):
    with pytest.raises(PayloadParseError, match="message is required"):
        parse_chat_send_params(params)


@pytest.mark.parametrize(
    "params",
    [
        {"message": {"content": [{"type": "input_audio", "data": "DUMMY_AUDIO"}]}},
        {"message": {"content": [{"type": "input_image", "data": "DUMMY_IMAGE"}]}},
        {"message": {"content": [{"type": "text", "text": "hello", "data": "DUMMY_AUDIO"}]}},
        {"content": [{"type": "image", "data": "DUMMY_IMAGE"}]},
        {"content": [{"text": "hello"}]},
        {"content": b"DUMMY_BINARY_AUDIO"},
    ],
)
def test_chat_media_content_raises_explicit_unsupported_media(params):
    with pytest.raises(PayloadParseError) as exc_info:
        parse_chat_send_params(params)

    assert exc_info.value.code == "UNSUPPORTED_MEDIA"
    assert str(exc_info.value) == "unsupported media content"
    assert "DUMMY" not in str(exc_info.value)


def test_request_params_rejects_malformed_payload_shape_without_echoing_secret():
    frame = {
        "type": "req",
        "id": "bad-1",
        "method": "chat.send",
        "payload": ["DUMMY_DEVICE_TOKEN_DO_NOT_USE"],
    }

    with pytest.raises(PayloadParseError) as exc_info:
        request_params(frame)

    assert exc_info.value.code == "BAD_REQUEST"
    assert "payload must be an object" in str(exc_info.value)
    assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in str(exc_info.value)


def test_nested_content_message_text_is_extracted_from_text_parts():
    request = parse_chat_send_params(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": " world"},
                ]
            },
            "session": {"key": "nested-session"},
            "requestId": "nested-run",
        }
    )

    assert request.message == "hello world"
    assert request.session_key == "nested-session"
    assert request.idempotency_key == "nested-run"


def test_single_mapping_text_content_part_is_extracted():
    request = parse_chat_send_params(
        {
            "message": {"content": {"type": "input_text", "text": "hello single part"}},
            "sessionKey": "single-part-session",
            "runId": "single-part-run",
        }
    )

    assert request.message == "hello single part"
    assert request.session_key == "single-part-session"
    assert request.idempotency_key == "single-part-run"


def test_mixed_text_and_media_content_is_not_silently_dropped():
    with pytest.raises(PayloadParseError) as exc_info:
        parse_chat_send_params(
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image", "data": "DUMMY_IMAGE"},
                    ]
                },
                "session": {"key": "nested-session"},
                "requestId": "nested-run",
            }
        )

    assert exc_info.value.code == "UNSUPPORTED_MEDIA"
    assert "DUMMY_IMAGE" not in str(exc_info.value)
