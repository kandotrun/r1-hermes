from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

from .adapter import (
    DEFAULT_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS,
    DEFAULT_DEVICE_TOKEN_MAX_AGE_SECONDS,
    DEFAULT_GLOBAL_CONCURRENCY,
    STATE_FILE,
    DeviceState,
    R1HermesAdapter,
    R1HermesConfig,
)
from .hermes_runner import HermesCliRunner
from .qr import build_pairing_payload, write_qr_png
from .r1_client import R1ProbeClient
from .toolsets import high_impact_toolset_error, high_impact_toolsets

TOKEN_BYTES = 32
TOKEN_ENV_NAME = "R1_HERMES_GATEWAY_TOKEN"  # noqa: S105 - env var name, not a secret


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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
        "--timeout", type=float, default=float(os.environ.get("R1_HERMES_TIMEOUT", "180"))
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
    if args.command in {"payload", "qr", "probe"} and not args.token:
        raise SystemExit("--token or R1_HERMES_GATEWAY_TOKEN is required")
    if args.command in {"serve", "hermes"}:
        token = os.environ.get("R1_HERMES_GATEWAY_TOKEN", "")
        if not token:
            raise SystemExit("R1_HERMES_GATEWAY_TOKEN is required")
        try:
            config = R1HermesConfig.from_env(
                state_dir=Path(args.state_dir),
                host=args.host,
                port=args.port,
                allow_public_bind=args.allow_public_bind,
                per_device_concurrency=args.per_device_concurrency,
                global_concurrency=args.global_concurrency,
                device_token_max_age_seconds=args.device_token_max_age_seconds,
                device_token_idle_timeout_seconds=args.device_token_idle_timeout_seconds,
                allow_remote_health=args.allow_remote_health,
                health_diagnostics=args.health_diagnostics,
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
    path = state_dir / STATE_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    devices = data.get("devices", {}) if isinstance(data, dict) else {}
    if not isinstance(devices, dict):
        return []
    return sorted(str(device_id) for device_id in devices)


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
