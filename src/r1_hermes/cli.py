from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from .adapter import R1HermesAdapter, R1HermesConfig
from .hermes_runner import HermesCliRunner
from .qr import build_pairing_payload, write_qr_png


async def _demo_handler(text: str, *, device_id: str, session_key: str) -> str:
    return f"[{device_id}/{session_key}] {text}"


def add_server_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", default=str(Path.home() / ".r1-hermes"))
    parser.add_argument("--host", default=os.environ.get("R1_HERMES_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("R1_HERMES_PORT", "18789")))


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

    args = parser.parse_args()
    if args.command in {"payload", "qr"} and not args.token:
        raise SystemExit("--token or R1_HERMES_GATEWAY_TOKEN is required")
    if args.command in {"serve", "hermes"}:
        token = os.environ.get("R1_HERMES_GATEWAY_TOKEN", "")
        if not token:
            raise SystemExit("R1_HERMES_GATEWAY_TOKEN is required")
        config = R1HermesConfig(
            gateway_token=token,
            state_dir=Path(args.state_dir),
            host=args.host,
            port=args.port,
        )
        if args.command == "serve":
            message_handler = _demo_handler
        else:
            message_handler = HermesCliRunner(
                command=(args.hermes_command,),
                timeout_seconds=args.timeout,
                toolsets=args.toolsets or None,
                continue_sessions=not args.no_continue,
            )
        adapter = R1HermesAdapter(config, message_handler=message_handler)
        asyncio.run(_run_forever(adapter))
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
        path = write_qr_png(payload_text, Path(args.output))
        print(f"Wrote secret QR PNG: {path}")


async def _run_forever(adapter: R1HermesAdapter) -> None:
    await adapter.start()
    try:
        print(f"r1-hermes listening on {adapter.config.host}:{adapter.config.port}")
        while True:
            await asyncio.sleep(3600)
    finally:
        await adapter.stop()


if __name__ == "__main__":
    main()
