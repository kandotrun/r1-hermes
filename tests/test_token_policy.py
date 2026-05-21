import pytest

from r1_hermes.token_policy import (
    GatewayTokenWeakError,
    require_strong_gateway_token,
    validate_gateway_token_strength,
)

from .token_fixtures import STRONG_GATEWAY_TOKEN


def test_strong_gateway_token_is_accepted():
    assert validate_gateway_token_strength(STRONG_GATEWAY_TOKEN).ok is True
    assert require_strong_gateway_token(STRONG_GATEWAY_TOKEN) == STRONG_GATEWAY_TOKEN


@pytest.mark.parametrize(
    "weak_token",
    [
        "DUMMY_GATEWAY_TOKEN_DO_NOT_USE",
        "gateway-secret",
        "passwordpasswordpasswordpassword",
        "1234567890123456789012345678901234567890123",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_weak_gateway_token_error_is_redacted(weak_token):
    strength = validate_gateway_token_strength(weak_token)

    assert strength.ok is False
    with pytest.raises(GatewayTokenWeakError) as exc_info:
        require_strong_gateway_token(weak_token)

    error = str(exc_info.value)
    assert "gateway token strength" in error
    assert "token_urlsafe(32)" in error
    assert weak_token not in error
