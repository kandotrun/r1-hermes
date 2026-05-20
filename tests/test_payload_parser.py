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
        {"message": {"content": [{"type": "input_audio", "data": "ignored"}]}},
    ],
)
def test_chat_message_is_required(params):
    with pytest.raises(PayloadParseError, match="message is required"):
        parse_chat_send_params(params)


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


def test_nested_content_message_text_is_extracted_from_text_parts_only():
    request = parse_chat_send_params(
        {
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image", "data": "ignored"},
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
