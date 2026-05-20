from __future__ import annotations

from collections.abc import Iterable, Sequence

HIGH_IMPACT_TOOLSETS = frozenset(
    {
        "automotive",
        "browser",
        "browser-automation",
        "car",
        "computer",
        "computer-use",
        "desktop",
        "device",
        "file",
        "files",
        "filesystem",
        "home",
        "home-automation",
        "shell",
        "smart-home",
        "smarthome",
        "terminal",
        "vehicle",
    }
)

HIGH_IMPACT_TOOLSET_ERROR = (
    "Refusing high-impact Hermes toolsets for a Rabbit R1 session: {toolsets}. "
    "Review the network boundary, pairing flow, physical access model, and command/data access "
    "risk first. Then pass --allow-high-impact-toolsets or set "
    "R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1 if this deployment is intentionally approved."
)


def parse_toolsets(toolsets: str | Iterable[str] | None) -> tuple[str, ...]:
    if toolsets is None:
        return ()
    if isinstance(toolsets, str):
        parts = toolsets.replace(";", ",").split(",")
    else:
        parts = []
        for item in toolsets:
            parts.extend(str(item).replace(";", ",").split(","))
    return tuple(part.strip() for part in parts if part and part.strip())


def high_impact_toolsets(toolsets: str | Iterable[str] | None) -> tuple[str, ...]:
    risky: list[str] = []
    seen = set()
    for toolset in parse_toolsets(toolsets):
        normalized = _normalize_toolset(toolset)
        if normalized in HIGH_IMPACT_TOOLSETS and normalized not in seen:
            risky.append(toolset)
            seen.add(normalized)
    return tuple(risky)


def validate_toolsets(
    toolsets: str | Iterable[str] | None,
    *,
    allow_high_impact_toolsets: bool = False,
) -> tuple[str, ...]:
    requested = parse_toolsets(toolsets)
    if not allow_high_impact_toolsets:
        risky = high_impact_toolsets(requested)
        if risky:
            raise ValueError(high_impact_toolset_error(risky))
    return requested


def sanitize_platform_toolsets(
    toolsets: Sequence[str] | str | None,
    *,
    allow_high_impact_toolsets: bool = False,
) -> tuple[str, ...]:
    requested = parse_toolsets(toolsets)
    if allow_high_impact_toolsets:
        return requested
    return tuple(
        toolset
        for toolset in requested
        if _normalize_toolset(toolset) not in HIGH_IMPACT_TOOLSETS
    )


def toolsets_to_cli_arg(toolsets: str | Iterable[str] | None) -> str | None:
    requested = parse_toolsets(toolsets)
    if not requested:
        return None
    return ",".join(requested)


def high_impact_toolset_error(toolsets: Iterable[str]) -> str:
    names = ",".join(toolsets)
    return HIGH_IMPACT_TOOLSET_ERROR.format(toolsets=names)


def _normalize_toolset(toolset: str) -> str:
    return str(toolset).strip().lower().replace("_", "-")
