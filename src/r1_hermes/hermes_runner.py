from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .chat_errors import ChatRunFailedError, ChatRunTimeoutError
from .toolsets import toolsets_to_cli_arg, validate_toolsets

ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]

logger = logging.getLogger(__name__)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def build_session_name(device_id: str, session_key: str) -> str:
    """Build a stable Hermes session name without trusting client-provided text."""
    raw = f"{device_id}:{session_key}"
    readable = _SAFE_CHARS.sub("-", raw).strip("-")[:36] or "device"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"r1-hermes-{readable}-{digest}"[:80]


@dataclass(frozen=True)
class HermesCliRunner:
    """Message handler that invokes `hermes chat` for each authenticated R1 message."""

    command: tuple[str, ...] = ("hermes",)
    timeout_seconds: float = 180
    toolsets: str | None = "safe"
    source: str = "r1-hermes"
    continue_sessions: bool = True
    allow_high_impact_toolsets: bool = False
    process_factory: ProcessFactory | None = None

    def __post_init__(self) -> None:
        requested = validate_toolsets(
            self.toolsets,
            allow_high_impact_toolsets=self.allow_high_impact_toolsets,
        )
        object.__setattr__(self, "toolsets", toolsets_to_cli_arg(requested))

    async def __call__(self, text: str, *, device_id: str, session_key: str) -> str:
        argv = [*self.command, "chat", "--quiet", "--source", self.source]
        if self.toolsets:
            argv.extend(["--toolsets", self.toolsets])
        if self.continue_sessions:
            argv.extend(["--continue", build_session_name(device_id, session_key)])
        argv.extend(["--query", text])

        factory = self.process_factory or asyncio.create_subprocess_exec
        process = await factory(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except (TimeoutError, asyncio.TimeoutError):
            process.kill()
            try:
                await process.wait()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("failed while waiting for timed-out Hermes process")
            raise ChatRunTimeoutError from None

        if process.returncode != 0:
            logger.warning(
                "Hermes command failed with exit code %s; stderr length=%d",
                process.returncode,
                len(stderr or b""),
            )
            raise ChatRunFailedError

        output = stdout.decode("utf-8", errors="replace").strip()
        return output or "Hermes returned an empty response."
