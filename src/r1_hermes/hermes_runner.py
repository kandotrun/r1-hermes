from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .audit import audit_log, hash_identifier
from .chat_errors import ChatRunFailedError, ChatRunTimeoutError
from .outbound import DEFAULT_OUTBOUND_TEXT_MAX_CHARS, bound_outbound_text
from .toolsets import toolsets_to_cli_arg, validate_toolsets

ProcessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]

logger = logging.getLogger(__name__)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
HERMES_SMOKE_QUERY = "Reply with exactly OK"


def build_session_name(device_id: str, session_key: str) -> str:
    """Build a stable Hermes session name without trusting client-provided text."""
    raw = f"{device_id}:{session_key}"
    readable = _SAFE_CHARS.sub("-", raw).strip("-")[:36] or "device"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"r1-hermes-{readable}-{digest}"[:80]


@dataclass(frozen=True)
class HermesSmokeResult:
    ok: bool
    returncode: int | None = None
    stdout: str = ""
    stderr_bytes: int = 0
    error: str = ""


async def run_hermes_smoke(
    *,
    command: tuple[str, ...] = ("hermes",),
    timeout_seconds: float = 30,
    process_factory: ProcessFactory | None = None,
) -> HermesSmokeResult:
    """Run a safe Hermes availability smoke test without invoking a shell."""
    argv = [
        *command,
        "chat",
        "--quiet",
        "--source",
        "r1-hermes-smoke",
        "--toolsets",
        "safe",
        "--query",
        HERMES_SMOKE_QUERY,
    ]
    factory = process_factory or asyncio.create_subprocess_exec
    try:
        process = await factory(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return HermesSmokeResult(False, error="Hermes command not found")
    except OSError as exc:
        return HermesSmokeResult(False, error=f"Hermes command failed to start: {exc.strerror}")

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except (TimeoutError, asyncio.TimeoutError):
        process.kill()
        try:
            await process.wait()
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.exception("failed while waiting for timed-out Hermes smoke process")
        return HermesSmokeResult(False, error="Hermes smoke command timed out")

    output = stdout.decode("utf-8", errors="replace").strip()
    stderr_bytes = len(stderr or b"")
    if process.returncode != 0:
        return HermesSmokeResult(
            False,
            returncode=process.returncode,
            stdout=output,
            stderr_bytes=stderr_bytes,
            error=f"Hermes smoke command exited with status {process.returncode}",
        )
    return HermesSmokeResult(
        True,
        returncode=process.returncode,
        stdout=output,
        stderr_bytes=stderr_bytes,
    )


@dataclass(frozen=True)
class HermesCliRunner:
    """Message handler that invokes `hermes chat` for each authenticated R1 message."""

    command: tuple[str, ...] = ("hermes",)
    timeout_seconds: float = 180
    toolsets: str | None = "safe"
    source: str = "r1-hermes"
    continue_sessions: bool = True
    allow_high_impact_toolsets: bool = False
    output_max_chars: int = DEFAULT_OUTBOUND_TEXT_MAX_CHARS
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
        except asyncio.CancelledError:
            process.kill()
            try:
                await process.wait()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("failed while waiting for cancelled Hermes process")
            audit_log(
                "info",
                "hermes.subprocess_cancelled",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
            )
            raise
        except (TimeoutError, asyncio.TimeoutError):
            process.kill()
            try:
                await process.wait()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.exception("failed while waiting for timed-out Hermes process")
            audit_log(
                "warning",
                "hermes.subprocess_timeout",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                timeout_seconds=self.timeout_seconds,
            )
            raise ChatRunTimeoutError from None

        if process.returncode != 0:
            audit_log(
                "warning",
                "hermes.subprocess_failed",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                returncode=process.returncode,
                stderr_bytes=len(stderr or b""),
            )
            raise ChatRunFailedError

        output = stdout.decode("utf-8", errors="replace").strip()
        bounded_output = bound_outbound_text(
            output or "Hermes returned an empty response.",
            max_chars=self.output_max_chars,
        )
        if bounded_output.truncated:
            audit_log(
                "warning",
                "hermes.stdout_truncated",
                device_id_hash=hash_identifier(device_id),
                session_key_hash=hash_identifier(session_key),
                stdout_bytes=len(stdout or b""),
                original_chars=bounded_output.original_chars,
                returned_chars=bounded_output.returned_chars,
                output_max_chars=bounded_output.max_chars,
            )
        return bounded_output.text
