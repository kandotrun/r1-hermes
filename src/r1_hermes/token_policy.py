from __future__ import annotations

import math
import re
from dataclasses import dataclass

MIN_GATEWAY_TOKEN_CHARS = 43
MIN_GATEWAY_TOKEN_UNIQUE_CHARS = 16
MIN_GATEWAY_TOKEN_SHANNON_BITS = 120.0
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_PLACEHOLDER_MARKERS = (
    "dummy",
    "example",
    "placeholder",
    "replace",
    "changeme",
    "change-me",
    "do-not-use",
    "do_not_use",
    "test-token",
    "gateway-secret",
    "gateway-token",
    "secret-token",
    "password",
    "letmein",
)
_KEYBOARD_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "zyxwvutsrqponmlkjihgfedcba",
    "0123456789",
    "9876543210",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
)


@dataclass(frozen=True)
class GatewayTokenStrength:
    ok: bool
    reasons: tuple[str, ...]


class GatewayTokenWeakError(ValueError):
    """Raised when a configured gateway token is missing or too weak to use."""


def validate_gateway_token_strength(token: str) -> GatewayTokenStrength:
    reasons: list[str] = []
    value = str(token or "")
    stripped = value.strip()
    lowered = stripped.lower()

    if not stripped:
        return GatewayTokenStrength(False, ("missing",))
    if stripped != value:
        reasons.append("surrounding whitespace")
    if len(stripped) < MIN_GATEWAY_TOKEN_CHARS:
        reasons.append(f"shorter than {MIN_GATEWAY_TOKEN_CHARS} characters")
    if not _TOKEN_PATTERN.fullmatch(stripped):
        reasons.append("contains characters outside URL-safe token format")
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        reasons.append("known placeholder or dummy value")
    if len(set(stripped)) < MIN_GATEWAY_TOKEN_UNIQUE_CHARS:
        reasons.append("too few distinct characters")
    if _has_long_repeated_run(stripped):
        reasons.append("repeated character run")
    if _has_sequential_run(lowered):
        reasons.append("sequential keyboard-looking run")
    if _shannon_bits(stripped) < MIN_GATEWAY_TOKEN_SHANNON_BITS:
        reasons.append(f"estimated entropy below {int(MIN_GATEWAY_TOKEN_SHANNON_BITS)} bits")

    return GatewayTokenStrength(not reasons, tuple(dict.fromkeys(reasons)))


def require_strong_gateway_token(token: str, *, context: str = "gateway token") -> str:
    strength = validate_gateway_token_strength(token)
    if strength.ok:
        return token
    reason_text = "; ".join(strength.reasons)
    raise GatewayTokenWeakError(
        f"{context} strength check failed: {reason_text}. Value redacted; "
        f"{_token_generation_help()}."
    )


def gateway_token_failure_detail(reasons: tuple[str, ...]) -> str:
    reason_text = "; ".join(reasons)
    return (
        "gateway token strength check failed: "
        f"{reason_text}; value redacted; {_token_generation_help()}"
    )


def _token_generation_help() -> str:
    return "generate one with secrets.token_urlsafe(32)"


def _has_long_repeated_run(token: str) -> bool:
    previous = ""
    count = 0
    for char in token:
        if char == previous:
            count += 1
        else:
            previous = char
            count = 1
        if count >= 8:
            return True
    return False


def _has_sequential_run(token: str) -> bool:
    compact = token.replace("-", "").replace("_", "")
    for sequence in _KEYBOARD_SEQUENCES:
        for size in range(12, len(sequence) + 1):
            for start in range(0, len(sequence) - size + 1):
                if sequence[start : start + size] in compact:
                    return True
    return False


def _shannon_bits(token: str) -> float:
    if not token:
        return 0.0
    length = len(token)
    counts = {char: token.count(char) for char in set(token)}
    entropy_per_char = -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )
    return entropy_per_char * length
