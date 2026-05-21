from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import secrets
import socket
import stat
import sys
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .adapter import (
    DEFAULT_AUTHENTICATED_CONNECTION_LIMIT,
    DEFAULT_AUTHENTICATED_IDLE_TIMEOUT_SECONDS,
    DEFAULT_AUTHENTICATED_MAX_LIFETIME_SECONDS,
    DEFAULT_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT,
    DEFAULT_CHAT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_CHAT_RUN_TIMEOUT_SECONDS,
    DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS,
    DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS,
    DEFAULT_GLOBAL_CONCURRENCY,
    DEFAULT_IDEMPOTENCY_CACHE_MAX_ENTRIES,
    DEFAULT_IDEMPOTENCY_CACHE_TTL_SECONDS,
    PUBLIC_BIND_ERROR,
    STATE_DIGEST_KEY_FILE,
    STATE_FILE,
    DeviceState,
    R1HermesAdapter,
    R1HermesConfig,
    _is_wildcard_public_bind,
)
from .hermes_runner import HERMES_SMOKE_QUERY, HermesCliRunner, run_hermes_smoke
from .media import DEFAULT_MEDIA_MAX_BYTES, DEFAULT_MEDIA_TTL_SECONDS
from .qr import build_pairing_payload, write_qr_png
from .r1_client import R1ProbeClient
from .token_policy import (
    gateway_token_failure_detail,
    require_strong_gateway_token,
    validate_gateway_token_strength,
)
from .toolsets import high_impact_toolset_error, high_impact_toolsets, parse_toolsets

TOKEN_BYTES = 32
TOKEN_ENV_NAME = "R1_HERMES_GATEWAY_TOKEN"  # noqa: S105 - env var name, not a secret
SYSTEMD_SERVICE_ASSET = "r1-hermes.service"
SYSTEMD_ENV_ASSET = "r1-hermes.env.example"
DEFAULT_R1_TOOLSETS = ("safe",)
DEFAULT_SLACK_EQUIVALENT_TOOLSETS = ("safe", "web", "terminal", "file")
_create_subprocess_exec = asyncio.create_subprocess_exec


class DoctorSeverity(str, Enum):
    PASS = "PASS"  # noqa: S105 - status label, not a credential
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class DoctorCheck:
    severity: DoctorSeverity
    name: str
    detail: str


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _configure_logging() -> None:
    level_name = os.environ.get("R1_HERMES_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")


async def _demo_handler(text: str, *, device_id: str, session_key: str) -> str:
    return f"[{device_id}/{session_key}] {text}"


def add_server_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    parser.add_argument("--host", default=os.environ.get("R1_HERMES_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("R1_HERMES_PORT", "18789")))
    parser.add_argument(
        "--ready-file",
        default="",
        help="Write this file after the gateway starts; useful for smoke tests and supervisors",
    )
    parser.add_argument(
        "--global-concurrency",
        type=int,
        default=int(
            os.environ.get("R1_HERMES_GLOBAL_CONCURRENCY", str(DEFAULT_GLOBAL_CONCURRENCY))
        ),
        help="Maximum total authenticated chat runs allowed at once across all devices",
    )
    parser.add_argument(
        "--per-device-concurrency",
        type=int,
        default=int(os.environ.get("R1_HERMES_PER_DEVICE_CONCURRENCY", "1")),
        help="Maximum authenticated chat runs allowed at once for one device",
    )
    parser.add_argument(
        "--authenticated-connection-limit",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT",
                str(DEFAULT_AUTHENTICATED_CONNECTION_LIMIT),
            )
        ),
        help="Maximum total authenticated WebSocket connections allowed at once",
    )
    parser.add_argument(
        "--authenticated-per-device-connection-limit",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT",
                str(DEFAULT_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT),
            )
        ),
        help="Maximum authenticated WebSocket connections allowed at once for one device",
    )
    parser.add_argument(
        "--authenticated-idle-timeout-seconds",
        type=float,
        default=float(
            os.environ.get(
                "R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS",
                str(DEFAULT_AUTHENTICATED_IDLE_TIMEOUT_SECONDS),
            )
        ),
        help="Seconds an authenticated idle WebSocket may remain open without chat work",
    )
    parser.add_argument(
        "--authenticated-max-lifetime-seconds",
        type=float,
        default=float(
            os.environ.get(
                "R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS",
                str(DEFAULT_AUTHENTICATED_MAX_LIFETIME_SECONDS),
            )
        ),
        help="Maximum authenticated WebSocket lifetime in seconds",
    )
    parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        default=_env_flag("R1_HERMES_ALLOW_PUBLIC_BIND"),
        help="Explicitly allow wildcard binds such as 0.0.0.0 or :: after reviewing exposure",
    )
    parser.add_argument(
        "--allow-remote-health",
        action="store_true",
        default=_env_flag("R1_HERMES_ALLOW_REMOTE_HEALTH"),
        help="Allow /healthz requests from non-local peers after reviewing exposure",
    )
    parser.add_argument(
        "--health-diagnostics",
        action="store_true",
        default=_env_flag("R1_HERMES_HEALTH_DIAGNOSTICS"),
        help="Include diagnostic paired-device counts in /healthz responses",
    )
    parser.add_argument(
        "--frame-shape-logging",
        action="store_true",
        default=_env_flag("R1_HERMES_FRAME_SHAPE_LOGGING"),
        help="Log sanitized request frame shapes for Rabbit/OpenClaw compatibility debugging",
    )
    parser.add_argument(
        "--tls-cert-file",
        default=os.environ.get("R1_HERMES_TLS_CERT_FILE", ""),
        help="PEM certificate chain for native TLS/WSS listener; requires --tls-key-file",
    )
    parser.add_argument(
        "--tls-key-file",
        default=os.environ.get("R1_HERMES_TLS_KEY_FILE", ""),
        help="PEM private key for native TLS/WSS listener; requires --tls-cert-file",
    )
    parser.add_argument(
        "--allowed-device-id",
        action="append",
        default=None,
        dest="allowed_device_ids",
        help=(
            "Allow only this Rabbit R1 device.id to pair or reconnect; repeat for multiple "
            "devices. Defaults to R1_HERMES_ALLOWED_DEVICE_IDS when omitted."
        ),
    )
    parser.add_argument(
        "--idempotency-cache-max-entries",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_IDEMPOTENCY_CACHE_MAX_ENTRIES",
                str(DEFAULT_IDEMPOTENCY_CACHE_MAX_ENTRIES),
            )
        ),
        help="Maximum in-memory chat.send idempotency keys to keep; 0 disables dedupe",
    )
    parser.add_argument(
        "--idempotency-cache-ttl-seconds",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_IDEMPOTENCY_CACHE_TTL_SECONDS",
                str(DEFAULT_IDEMPOTENCY_CACHE_TTL_SECONDS),
            )
        ),
        help="Seconds to keep completed chat.send idempotency keys in memory",
    )
    parser.add_argument(
        "--media-max-file-bytes",
        type=int,
        default=int(os.environ.get("R1_HERMES_MEDIA_MAX_FILE_BYTES", str(DEFAULT_MEDIA_MAX_BYTES))),
        help="Maximum bytes per accepted image attachment before Hermes is invoked",
    )
    parser.add_argument(
        "--media-ttl-seconds",
        type=int,
        default=int(os.environ.get("R1_HERMES_MEDIA_TTL_SECONDS", str(DEFAULT_MEDIA_TTL_SECONDS))),
        help="Seconds before stale private media uploads are pruned",
    )
    add_device_expiry_args(parser)


def add_device_expiry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device-token-max-age-seconds",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS",
                str(DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS),
            )
        ),
        help="Maximum device-token lifetime; 0 disables max-age expiration",
    )
    parser.add_argument(
        "--device-token-idle-timeout-seconds",
        type=int,
        default=int(
            os.environ.get(
                "R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS",
                str(DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS),
            )
        ),
        help="Maximum idle time before device-token expiration; 0 disables idle expiration",
    )


def add_doctor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    parser.add_argument("--host", default=os.environ.get("R1_HERMES_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("R1_HERMES_PORT", "18789")))
    parser.add_argument(
        "--toolsets",
        default=os.environ.get("R1_HERMES_TOOLSETS", _format_toolsets(DEFAULT_R1_TOOLSETS)),
        help="Effective Rabbit R1 Hermes toolsets to check, defaulting to R1_HERMES_TOOLSETS",
    )
    parser.add_argument(
        "--slack-equivalent-toolsets",
        default=os.environ.get(
            "R1_HERMES_SLACK_EQUIVALENT_TOOLSETS",
            _format_toolsets(DEFAULT_SLACK_EQUIVALENT_TOOLSETS),
        ),
        help=(
            "Configured Slack-equivalent toolset bundle for parity checks; defaults to "
            "R1_HERMES_SLACK_EQUIVALENT_TOOLSETS or the built-in bundle"
        ),
    )
    parser.add_argument(
        "--require-slack-equivalent-toolsets",
        action="store_true",
        help="Fail diagnostics unless the effective R1 toolsets exactly match the Slack bundle",
    )
    parser.add_argument(
        "--allow-high-impact-toolsets",
        action="store_true",
        default=_env_flag("R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS"),
        help="Acknowledge high-impact R1 toolsets during diagnostics after review",
    )
    parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        default=_env_flag("R1_HERMES_ALLOW_PUBLIC_BIND"),
        help="Acknowledge an intentionally reviewed wildcard bind during diagnostics",
    )
    parser.add_argument("--hermes-command", default=os.environ.get("R1_HERMES_COMMAND", "hermes"))
    parser.add_argument(
        "--hermes-timeout",
        type=float,
        default=float(os.environ.get("R1_HERMES_DOCTOR_HERMES_TIMEOUT", "30")),
        help="Seconds to wait for the safe Hermes smoke command",
    )
    parser.add_argument(
        "--skip-hermes-smoke",
        action="store_true",
        help="Skip the Hermes CLI smoke command and report a warning instead",
    )
    parser.add_argument("--url", help="Optional WebSocket URL to probe without printing secrets")
    parser.add_argument("--token", default=os.environ.get("R1_HERMES_GATEWAY_TOKEN", ""))
    parser.add_argument("--device-id", default="r1-doctor")
    parser.add_argument("--session-key", default="doctor")
    parser.add_argument(
        "--probe-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the optional WebSocket probe",
    )
    parser.add_argument(
        "--connect-method",
        choices=["connect", "gateway.connect"],
        default="connect",
        help="Handshake variant to exercise when --url is provided",
    )
    parser.add_argument(
        "--qr-output",
        help="Optional planned QR PNG output path to check without generating a QR",
    )


def add_install_systemd_user_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--unit-output",
        default=str(Path.home() / ".config" / "systemd" / "user" / "r1-hermes.service"),
        help="Destination path for the systemd user unit template",
    )
    parser.add_argument(
        "--env-output",
        default=str(Path.home() / ".config" / "r1-hermes" / "r1-hermes.env"),
        help="Destination path for the secret-bearing environment file template",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files instead of failing closed",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="r1-hermes")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run a standalone demo gateway")
    add_server_args(serve)

    hermes = sub.add_parser("hermes", help="Run the Rabbit R1 gateway backed by Hermes Agent")
    add_server_args(hermes)
    hermes.add_argument("--hermes-command", default=os.environ.get("R1_HERMES_COMMAND", "hermes"))
    hermes.add_argument("--toolsets", default=os.environ.get("R1_HERMES_TOOLSETS", "safe"))
    hermes.add_argument(
        "--allow-high-impact-toolsets",
        action="store_true",
        default=_env_flag("R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS"),
        help="Explicitly allow high-impact Hermes toolsets such as terminal or file",
    )
    hermes.add_argument(
        "--timeout",
        type=float,
        default=float(
            os.environ.get(
                "R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS",
                os.environ.get("R1_HERMES_TIMEOUT", str(DEFAULT_CHAT_RUN_TIMEOUT_SECONDS)),
            )
        ),
        help="Maximum seconds an authenticated R1 chat run may occupy the gateway",
    )
    hermes.add_argument(
        "--heartbeat-interval",
        type=float,
        default=float(
            os.environ.get(
                "R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS",
                str(DEFAULT_CHAT_HEARTBEAT_INTERVAL_SECONDS),
            )
        ),
        help="Seconds between generic running events while Hermes is still working",
    )
    hermes.add_argument(
        "--no-continue",
        action="store_true",
        help="Do not resume a stable Hermes session per R1 device/session key",
    )

    payload = sub.add_parser("payload", help="Print a Rabbit R1 QR payload JSON")
    payload.add_argument("--host", action="append", required=True, dest="hosts")
    payload.add_argument("--port", type=int, default=18789)
    payload.add_argument("--token", default=os.environ.get("R1_HERMES_GATEWAY_TOKEN", ""))
    payload.add_argument("--protocol", choices=["ws", "wss"], default="ws")

    qr = sub.add_parser("qr", help="Write a Rabbit R1 QR PNG")
    qr.add_argument("--host", action="append", required=True, dest="hosts")
    qr.add_argument("--port", type=int, default=18789)
    qr.add_argument("--token", default=os.environ.get("R1_HERMES_GATEWAY_TOKEN", ""))
    qr.add_argument("--protocol", choices=["ws", "wss"], default="ws")
    qr.add_argument("--output", required=True)
    qr.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing QR PNG at --output instead of failing closed",
    )
    qr.add_argument(
        "--print-payload",
        action="store_true",
        help="Also print the secret QR payload JSON to stdout",
    )

    revoke = sub.add_parser("revoke", help="Revoke a paired Rabbit R1 device token")
    revoke.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    revoke_target = revoke.add_mutually_exclusive_group(required=True)
    revoke_target.add_argument("--device-id")
    revoke_target.add_argument(
        "--all", action="store_true", help="Revoke every paired device token in the state file"
    )
    revoke.add_argument(
        "--dry-run",
        action="store_true",
        help="Show affected device IDs without modifying the state file",
    )

    rotate = sub.add_parser("rotate", help="Rotate the gateway token and revoke paired devices")
    rotate.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    rotate.add_argument(
        "--env-file",
        help="Update R1_HERMES_GATEWAY_TOKEN in a systemd-style environment file",
    )
    rotate.add_argument(
        "--print-token",
        action="store_true",
        help="Print the new gateway token; this writes a bearer secret to stdout",
    )
    rotate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned token rotation and device revocation without changing files",
    )

    cleanup = sub.add_parser("cleanup", help="Prune expired Rabbit R1 device records")
    cleanup.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    add_device_expiry_args(cleanup)

    install_systemd = sub.add_parser(
        "install-systemd-user",
        help="Install packaged systemd user-service and env templates",
    )
    add_install_systemd_user_args(install_systemd)

    doctor = sub.add_parser("doctor", help="Run secret-safe setup and pairing diagnostics")
    add_doctor_args(doctor)

    probe = sub.add_parser("probe", help="Send a Rabbit R1-style probe message to a gateway")
    probe.add_argument("--url", required=True, help="WebSocket URL, e.g. ws://100.x.y.z:18789/")
    probe.add_argument("--token", default=os.environ.get("R1_HERMES_GATEWAY_TOKEN", ""))
    probe.add_argument("--device-id", default="r1-probe")
    probe.add_argument("--session-key", default="main")
    probe.add_argument("--message", required=True)
    probe.add_argument("--timeout", type=float, default=30.0)
    probe.add_argument(
        "--connect-method",
        choices=["connect", "gateway.connect"],
        default="connect",
        help="Handshake method to exercise during the probe",
    )
    probe.add_argument(
        "--dump-frames",
        action="store_true",
        help="Print redacted WebSocket frames for compatibility debugging",
    )

    args = parser.parse_args()
    if args.command in {"payload", "qr", "probe"}:
        if not args.token:
            raise SystemExit("--token or R1_HERMES_GATEWAY_TOKEN is required")
        try:
            require_strong_gateway_token(args.token, context="gateway token")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.command in {"serve", "hermes", "revoke", "rotate", "cleanup"}:
        _configure_logging()
    if args.command in {"serve", "hermes"}:
        token = os.environ.get("R1_HERMES_GATEWAY_TOKEN", "")
        if not token:
            raise SystemExit("R1_HERMES_GATEWAY_TOKEN is required")
        try:
            require_strong_gateway_token(token, context="gateway token")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            config = R1HermesConfig.from_env(
                state_dir=Path(args.state_dir),
                host=args.host,
                port=args.port,
                allow_public_bind=args.allow_public_bind,
                per_device_concurrency=args.per_device_concurrency,
                global_concurrency=args.global_concurrency,
                authenticated_connection_limit=args.authenticated_connection_limit,
                authenticated_per_device_connection_limit=(
                    args.authenticated_per_device_connection_limit
                ),
                authenticated_idle_timeout_seconds=args.authenticated_idle_timeout_seconds,
                authenticated_max_lifetime_seconds=args.authenticated_max_lifetime_seconds,
                device_token_max_age_seconds=args.device_token_max_age_seconds,
                device_token_idle_timeout_seconds=args.device_token_idle_timeout_seconds,
                idempotency_cache_max_entries=args.idempotency_cache_max_entries,
                idempotency_cache_ttl_seconds=args.idempotency_cache_ttl_seconds,
                chat_run_timeout_seconds=args.timeout if args.command == "hermes" else None,
                chat_heartbeat_interval_seconds=(
                    args.heartbeat_interval if args.command == "hermes" else None
                ),
                media_max_file_bytes=args.media_max_file_bytes,
                media_ttl_seconds=args.media_ttl_seconds,
                allow_remote_health=args.allow_remote_health,
                health_diagnostics=args.health_diagnostics,
                tls_cert_file=Path(args.tls_cert_file) if args.tls_cert_file else None,
                tls_key_file=Path(args.tls_key_file) if args.tls_key_file else None,
                allowed_device_ids=args.allowed_device_ids,
                frame_shape_logging=args.frame_shape_logging,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if args.command == "serve":
            message_handler = _demo_handler
        else:
            risky_toolsets = high_impact_toolsets(args.toolsets)
            if risky_toolsets and not args.allow_high_impact_toolsets:
                raise SystemExit(high_impact_toolset_error(risky_toolsets))
            message_handler = HermesCliRunner(
                command=(args.hermes_command,),
                timeout_seconds=args.timeout,
                toolsets=args.toolsets or None,
                continue_sessions=not args.no_continue,
                allow_high_impact_toolsets=args.allow_high_impact_toolsets,
            )
        try:
            adapter = R1HermesAdapter(config, message_handler=message_handler)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        asyncio.run(
            _run_forever(adapter, ready_file=Path(args.ready_file) if args.ready_file else None)
        )
    elif args.command == "payload":
        print(
            build_pairing_payload(
                hosts=args.hosts, port=args.port, token=args.token, protocol=args.protocol
            )
        )
    elif args.command == "qr":
        payload_text = build_pairing_payload(
            hosts=args.hosts, port=args.port, token=args.token, protocol=args.protocol
        )
        path = write_qr_png(payload_text, Path(args.output), overwrite=args.overwrite)
        print(f"Wrote secret QR PNG: {path}")
        if args.print_payload:
            print(payload_text)
    elif args.command == "probe":
        result = asyncio.run(
            R1ProbeClient(
                url=args.url,
                token=args.token,
                device_id=args.device_id,
                timeout_seconds=args.timeout,
                connect_method=args.connect_method,
                dump_frames=args.dump_frames,
                frame_sink=lambda line: print(line, file=sys.stderr),
            ).send_message(args.message, session_key=args.session_key)
        )
        print(result.response_text)
    elif args.command == "revoke":
        state_dir = Path(args.state_dir)
        if args.dry_run:
            device_ids = _read_state_device_ids(state_dir)
            if args.all:
                _print_revoke_summary("Would revoke", device_ids)
                return
            if args.device_id in device_ids:
                print(f"Would revoke device: {args.device_id}")
                return
            raise SystemExit(f"device not found: {args.device_id}")

        state = DeviceState(state_dir)
        if args.all:
            revoked = state.revoke_all()
            _print_revoke_summary("Revoked", revoked)
            return
        if not state.revoke(args.device_id):
            raise SystemExit(f"device not found: {args.device_id}")
        print(f"Revoked device: {args.device_id}")
    elif args.command == "rotate":
        if not args.dry_run and not args.env_file and not args.print_token:
            raise SystemExit("--env-file or --print-token is required to deliver the new token")
        state_dir = Path(args.state_dir)
        if args.dry_run:
            device_ids = _read_state_device_ids(state_dir)
            print("Would generate a new gateway token.")
            if args.env_file:
                print(f"Would update {TOKEN_ENV_NAME} in: {Path(args.env_file)}")
            if args.print_token:
                print("Would print the new gateway token because --print-token is set.")
            _print_revoke_summary("Would revoke", device_ids)
            return

        state = DeviceState(state_dir)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        if args.env_file:
            env_path = _update_env_file_token(Path(args.env_file), token)
            print(f"Rotated gateway token in: {env_path}")
        elif args.print_token:
            print("Rotated gateway token for stdout delivery.")
        if args.print_token:
            print(f"NEW {TOKEN_ENV_NAME} (SECRET): {token}")
        revoked = state.revoke_all()
        _print_revoke_summary(
            "Revoked", revoked, empty_message="No paired devices found; state unchanged."
        )
    elif args.command == "cleanup":
        state = DeviceState(
            Path(args.state_dir),
            device_token_max_age_seconds=args.device_token_max_age_seconds,
            device_token_idle_timeout_seconds=args.device_token_idle_timeout_seconds,
        )
        removed = state.prune_expired()
        print(f"Pruned expired devices: {removed}")
    elif args.command == "install-systemd-user":
        installed = _install_systemd_user_templates(
            unit_output=Path(args.unit_output),
            env_output=Path(args.env_output),
            overwrite=bool(args.overwrite),
        )
        print(f"Installed systemd user unit: {installed.unit_path}")
        print(f"Installed env template: {installed.env_path}")
        print("Edit the env file locally and replace the gateway-token placeholder before start.")
    elif args.command == "doctor":
        exit_code = asyncio.run(_run_doctor(args))
        if exit_code:
            raise SystemExit(exit_code)


async def _run_forever(adapter: R1HermesAdapter, *, ready_file: Path | None = None) -> None:
    await adapter.start()
    if ready_file is not None:
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text(f"{adapter.config.host}:{adapter.config.port}\n")
    try:
        print(f"r1-hermes listening on {adapter.config.host}:{adapter.config.port}")
        while True:
            await asyncio.sleep(3600)
    finally:
        await adapter.stop()


@dataclass(frozen=True)
class InstalledSystemdUserTemplates:
    unit_path: Path
    env_path: Path


def _systemd_asset_text(name: str) -> str:
    return (resources.files("r1_hermes") / "systemd" / name).read_text(encoding="utf-8")


def _write_template(path: Path, text: str, *, mode: int, overwrite: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def _check_template_destination(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SystemExit(f"file already exists; refusing to overwrite: {path}")


def _install_systemd_user_templates(
    *, unit_output: Path, env_output: Path, overwrite: bool = False
) -> InstalledSystemdUserTemplates:
    unit_text = _systemd_asset_text(SYSTEMD_SERVICE_ASSET)
    env_text = _systemd_asset_text(SYSTEMD_ENV_ASSET)
    _check_template_destination(unit_output, overwrite=overwrite)
    _check_template_destination(env_output, overwrite=overwrite)
    unit_path = _write_template(unit_output, unit_text, mode=0o644, overwrite=overwrite)
    env_path = _write_template(env_output, env_text, mode=0o600, overwrite=overwrite)
    return InstalledSystemdUserTemplates(unit_path=unit_path, env_path=env_path)


async def _run_doctor(args: argparse.Namespace) -> int:
    token = str(args.token or "")
    checks: list[DoctorCheck] = []
    checks.extend(_doctor_token_checks(token))
    checks.extend(_doctor_state_checks(Path(args.state_dir)))
    checks.extend(
        _doctor_host_port_checks(
            host=str(args.host),
            port=int(args.port),
            allow_public_bind=bool(args.allow_public_bind),
        )
    )
    checks.extend(
        _doctor_toolset_checks(
            toolsets=args.toolsets,
            slack_equivalent_toolsets=args.slack_equivalent_toolsets,
            allow_high_impact_toolsets=bool(args.allow_high_impact_toolsets),
            require_slack_equivalent_toolsets=bool(args.require_slack_equivalent_toolsets),
        )
    )
    checks.extend(_doctor_qr_output_checks(Path(args.qr_output)) if args.qr_output else [])

    if args.skip_hermes_smoke:
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "Hermes CLI smoke",
                "skipped by --skip-hermes-smoke; run it before pairing a real Rabbit R1",
            )
        )
    else:
        checks.extend(
            await _doctor_hermes_checks(
                command=str(args.hermes_command),
                timeout_seconds=float(args.hermes_timeout),
            )
        )

    if args.url:
        checks.extend(
            await _doctor_probe_checks(
                url=str(args.url),
                token=token,
                device_id=str(args.device_id),
                session_key=str(args.session_key),
                timeout_seconds=float(args.probe_timeout),
                connect_method=str(args.connect_method),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "Gateway probe",
                "skipped; pass --url after the gateway is running",
            )
        )

    _print_doctor_report(checks, secrets_to_redact=[token])
    return 1 if any(check.severity is DoctorSeverity.FAIL for check in checks) else 0


def _doctor_token_checks(token: str) -> list[DoctorCheck]:
    if not token:
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                TOKEN_ENV_NAME,
                "missing; create a fresh bearer token before starting the gateway or probing",
            )
        ]
    strength = validate_gateway_token_strength(token)
    if not strength.ok:
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "gateway token strength",
                gateway_token_failure_detail(strength.reasons),
            )
        ]
    return [
        DoctorCheck(
            DoctorSeverity.PASS,
            TOKEN_ENV_NAME,
            "present; value redacted and not printed",
        )
    ]


def _doctor_state_checks(state_dir: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if state_dir.is_symlink():
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "state directory",
                f"must not be a symlink: {state_dir}",
            )
        ]
    if not state_dir.exists():
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "state directory",
                f"does not exist yet; gateway will create it as 0700 at {state_dir}",
            )
        )
        return checks

    if not state_dir.is_dir():
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "state directory",
                f"is not a directory: {state_dir}",
            )
        ]

    dir_mode = _permission_bits(state_dir)
    if dir_mode & 0o077:
        checks.append(
            DoctorCheck(
                DoctorSeverity.FAIL,
                "state directory",
                f"mode {_format_mode(dir_mode)} is unsafe; expected 0700 for {state_dir}",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "state directory",
                f"mode {_format_mode(dir_mode)} keeps paired-device state owner-only",
            )
        )

    checks.extend(_doctor_secret_file_check(state_dir / STATE_FILE, "devices.json"))
    checks.extend(_doctor_secret_file_check(state_dir / STATE_DIGEST_KEY_FILE, "device HMAC key"))
    return checks


def _doctor_secret_file_check(path: Path, name: str) -> list[DoctorCheck]:
    if path.is_symlink():
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                name,
                f"must not be a symlink: {path}",
            )
        ]
    if not path.exists():
        return [
            DoctorCheck(
                DoctorSeverity.PASS,
                name,
                "not present yet; no stored device-token material to inspect",
            )
        ]
    if not path.is_file():
        return [DoctorCheck(DoctorSeverity.FAIL, name, f"is not a regular file: {path}")]

    mode = _permission_bits(path)
    if mode & 0o077:
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                name,
                f"mode {_format_mode(mode)} is unsafe; expected 0600 for {path}",
            )
        ]
    return [
        DoctorCheck(
            DoctorSeverity.PASS,
            name,
            f"mode {_format_mode(mode)} keeps local token material owner-only",
        )
    ]


def _doctor_host_port_checks(
    *, host: str, port: int, allow_public_bind: bool
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if not (1 <= port <= 65535):
        checks.append(DoctorCheck(DoctorSeverity.FAIL, "bind port", f"{port} is outside 1-65535"))
    else:
        checks.append(DoctorCheck(DoctorSeverity.PASS, "bind port", f"{port} is valid"))

    if not host.strip():
        checks.append(DoctorCheck(DoctorSeverity.FAIL, "bind host", "empty host is unsafe"))
        return checks

    if _is_wildcard_public_bind(host):
        if allow_public_bind:
            checks.append(
                DoctorCheck(
                    DoctorSeverity.WARN,
                    "bind host",
                    "wildcard bind acknowledged; verify firewall, client path, and QR address",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    DoctorSeverity.FAIL,
                    "bind host",
                    PUBLIC_BIND_ERROR.format(host=host),
                )
            )
        return checks

    host_class = _classify_host(host)
    if host_class == "loopback":
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "bind host",
                "loopback bind is safe for local smoke tests",
            )
        )
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "reachability hint",
                "Rabbit R1 needs a concrete Tailscale/LAN/proxy address before real pairing",
            )
        )
    elif host_class == "tailscale":
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "bind host",
                "specific Tailscale address selected; verify Rabbit R1 can reach it",
            )
        )
    elif host_class == "private":
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "bind host",
                "specific private address selected; verify Rabbit R1 can reach it",
            )
        )
    elif host_class == "public":
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "bind host",
                "address appears publicly routable; prefer Tailscale, mTLS, or IP allowlisting",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "bind host",
                "hostname could not be classified locally; verify it resolves to a narrow address",
            )
        )
    return checks


def _doctor_toolset_checks(
    *,
    toolsets: str | tuple[str, ...] | list[str] | None,
    slack_equivalent_toolsets: str | tuple[str, ...] | list[str] | None,
    allow_high_impact_toolsets: bool,
    require_slack_equivalent_toolsets: bool,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    requested = parse_toolsets(toolsets)
    slack_bundle = parse_toolsets(slack_equivalent_toolsets)
    effective = requested or DEFAULT_R1_TOOLSETS
    expected = slack_bundle or DEFAULT_SLACK_EQUIVALENT_TOOLSETS

    risky = high_impact_toolsets(effective)
    if risky:
        if allow_high_impact_toolsets:
            checks.append(
                DoctorCheck(
                    DoctorSeverity.PASS,
                    "high-impact toolset opt-in",
                    "requested high-impact toolsets "
                    f"{_format_toolsets(risky)} are explicitly allowed by "
                    "--allow-high-impact-toolsets or R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    DoctorSeverity.FAIL,
                    "high-impact toolset opt-in",
                    "requested high-impact toolsets "
                    f"{_format_toolsets(risky)}, but "
                    "R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS is not set; review the deployment "
                    "boundary, then pass --allow-high-impact-toolsets or set the env var",
                )
            )
    else:
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "high-impact toolset opt-in",
                "no high-impact R1 toolsets requested",
            )
        )

    missing, extra = _toolset_delta(effective, expected)
    effective_text = _format_toolsets(effective)
    expected_text = _format_toolsets(expected)
    if not missing and not extra:
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "Slack-equivalent toolsets",
                f"effective R1 toolsets match Slack-equivalent bundle: {effective_text}",
            )
        )
        return checks

    parts = [
        f"effective R1 toolsets {effective_text} differ from "
        f"Slack-equivalent bundle {expected_text}"
    ]
    if missing:
        parts.append(f"missing: {_format_toolsets(missing)}")
    if extra:
        parts.append(f"extra: {_format_toolsets(extra)}")
    parts.append(
        "use --toolsets or R1_HERMES_TOOLSETS to choose safe/minimal or intentionally reviewed "
        "Slack-equivalent mode"
    )
    severity = DoctorSeverity.FAIL if require_slack_equivalent_toolsets else DoctorSeverity.WARN
    checks.append(DoctorCheck(severity, "Slack-equivalent toolsets", "; ".join(parts)))
    return checks


def _toolset_delta(
    actual: tuple[str, ...], expected: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    actual_normalized = {_normalize_toolset_name(toolset) for toolset in actual}
    expected_normalized = {_normalize_toolset_name(toolset) for toolset in expected}
    missing = tuple(
        toolset
        for toolset in expected
        if _normalize_toolset_name(toolset) not in actual_normalized
    )
    extra = tuple(
        toolset
        for toolset in actual
        if _normalize_toolset_name(toolset) not in expected_normalized
    )
    return missing, extra


def _normalize_toolset_name(toolset: str) -> str:
    return str(toolset).strip().lower().replace("_", "-")


def _format_toolsets(toolsets: tuple[str, ...] | list[str]) -> str:
    return ",".join(toolsets) if toolsets else "(none)"


async def _doctor_hermes_checks(*, command: str, timeout_seconds: float) -> list[DoctorCheck]:
    result = await run_hermes_smoke(
        command=(command,),
        timeout_seconds=timeout_seconds,
        process_factory=_create_subprocess_exec,
    )
    if not result.ok:
        detail = result.error or "Hermes smoke command failed"
        if result.returncode is not None:
            detail = f"{detail}; stderr bytes={result.stderr_bytes}"
        return [DoctorCheck(DoctorSeverity.FAIL, "Hermes CLI smoke", detail)]
    if result.stdout.strip() != "OK":
        return [
            DoctorCheck(
                DoctorSeverity.WARN,
                "Hermes CLI smoke",
                "command completed but did not return exact OK; response text omitted",
            )
        ]
    return [DoctorCheck(DoctorSeverity.PASS, "Hermes CLI smoke", "safe toolset returned OK")]


async def _doctor_probe_checks(
    *,
    url: str,
    token: str,
    device_id: str,
    session_key: str,
    timeout_seconds: float,
    connect_method: str,
) -> list[DoctorCheck]:
    if not token:
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "Gateway probe",
                "cannot run without a gateway token; value would remain redacted",
            )
        ]
    safe_url = _redact_url(url)
    try:
        result = await R1ProbeClient(
            url=url,
            token=token,
            device_id=device_id,
            timeout_seconds=timeout_seconds,
            connect_method=connect_method,
            dump_frames=False,
            frame_sink=None,
        ).send_message(HERMES_SMOKE_QUERY, session_key=session_key)
    except Exception as exc:
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "Gateway probe",
                f"failed for {safe_url}: {type(exc).__name__}; details omitted",
            )
        ]
    if not getattr(result, "response_text", ""):
        return [
            DoctorCheck(
                DoctorSeverity.WARN,
                "Gateway probe",
                f"completed for {safe_url} but returned an empty response",
            )
        ]
    return [
        DoctorCheck(
            DoctorSeverity.PASS,
            "Gateway probe",
            f"completed for {safe_url}; response and device token omitted",
        )
    ]


def _doctor_qr_output_checks(output_path: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    parent = output_path.parent
    if output_path.is_symlink():
        return [
            DoctorCheck(
                DoctorSeverity.FAIL,
                "QR output path",
                f"must not be a symlink: {output_path}",
            )
        ]
    if output_path.exists():
        if output_path.is_dir():
            return [
                DoctorCheck(
                    DoctorSeverity.FAIL,
                    "QR output path",
                    f"is a directory; choose a new PNG path: {output_path}",
                )
            ]
        mode = _permission_bits(output_path)
        if mode & 0o077:
            return [
                DoctorCheck(
                    DoctorSeverity.FAIL,
                    "QR output path",
                    f"existing file mode {_format_mode(mode)} is unsafe; expected 0600",
                )
            ]
        return [
            DoctorCheck(
                DoctorSeverity.WARN,
                "QR output path",
                f"already exists at {output_path}; qr refuses overwrite unless --overwrite is set",
            )
        ]

    existing_parent = parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        checks.append(
            DoctorCheck(
                DoctorSeverity.FAIL,
                "QR output path",
                f"parent is not a directory: {existing_parent}",
            )
        )
        return checks

    parent_mode = _permission_bits(existing_parent)
    if parent_mode & 0o022:
        checks.append(
            DoctorCheck(
                DoctorSeverity.WARN,
                "QR output path",
                "parent "
                f"{_format_mode(parent_mode)} is writable beyond owner; "
                "QR file will still be 0600",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                DoctorSeverity.PASS,
                "QR output path",
                f"can create owner-only QR PNG at {output_path}",
            )
        )
    return checks


def _print_doctor_report(checks: list[DoctorCheck], *, secrets_to_redact: list[str]) -> None:
    print("r1-hermes doctor (secret-safe diagnostics)")
    for check in checks:
        detail = _redact_text(check.detail, secrets_to_redact)
        print(f"[{check.severity.value}] {check.name}: {detail}")
    passes = sum(1 for check in checks if check.severity is DoctorSeverity.PASS)
    warnings = sum(1 for check in checks if check.severity is DoctorSeverity.WARN)
    failures = sum(1 for check in checks if check.severity is DoctorSeverity.FAIL)
    exit_code = 1 if failures else 0
    print(
        f"Summary: {passes} pass, {warnings} warn, {failures} fail. "
        f"Exit code {exit_code}: non-zero only when FAIL checks are present."
    )


def _permission_bits(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _format_mode(mode: int) -> str:
    return f"0{mode:03o}"


def _classify_host(host: str) -> str:
    value = host.strip().strip("[]")
    if value.lower() == "localhost":
        return "loopback"
    try:
        addresses = [ipaddress.ip_address(value)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return "hostname"
        addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
    if all(address.is_loopback for address in addresses):
        return "loopback"
    if any(_is_tailscale_ipv4(address) for address in addresses):
        return "tailscale"
    if all(address.is_private or address.is_link_local for address in addresses):
        return "private"
    return "public"


def _is_tailscale_ipv4(address: ipaddress._BaseAddress) -> bool:
    tailscale_start = ipaddress.ip_address("100.64.0.0")
    tailscale_end = ipaddress.ip_address("100.127.255.255")
    return address.version == 4 and tailscale_start <= address <= tailscale_end


def _redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "[redacted-url]"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    query = "[redacted-query]" if parsed.query else ""
    path = "/" if parsed.path in {"", "/"} else "/[redacted-path]"
    return urlunsplit((parsed.scheme, netloc, path, query, ""))


def _redact_text(text: str, secrets_to_redact: list[str]) -> str:
    redacted = text
    for secret in secrets_to_redact:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted.replace(HERMES_SMOKE_QUERY, "[REDACTED_PROMPT]")


def _print_revoke_summary(
    prefix: str,
    device_ids: list[str],
    *,
    empty_message: str = "No paired devices found; state unchanged.",
) -> None:
    if not device_ids:
        print(empty_message)
        return
    print(f"{prefix} {len(device_ids)} device(s): {', '.join(device_ids)}")


def _read_state_device_ids(state_dir: Path) -> list[str]:
    return DeviceState.read_device_ids(state_dir)


def _update_env_file_token(path: Path, token: str) -> Path:
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    replacement = f"{TOKEN_ENV_NAME}={token}\n"
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        is_token_line = stripped.startswith(f"{TOKEN_ENV_NAME}=") or stripped.startswith(
            f"export {TOKEN_ENV_NAME}="
        )
        if is_token_line:
            indent = line[: len(line) - len(stripped)]
            prefix = "export " if stripped.startswith("export ") else ""
            new_lines.append(f"{indent}{prefix}{replacement}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = f"{new_lines[-1]}\n"
        new_lines.append(replacement)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text("".join(new_lines))
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    os.chmod(path, 0o600)
    return path


if __name__ == "__main__":
    main()
