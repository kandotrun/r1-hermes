from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

PAYLOAD_TYPE = "clawdbot-gateway"


def build_pairing_payload(
    *, hosts: Iterable[str], port: int, token: str, protocol: str = "ws"
) -> str:
    if protocol not in {"ws", "wss"}:
        raise ValueError("protocol must be 'ws' or 'wss'")
    host_list = [h.strip() for h in hosts if h and h.strip()]
    if not host_list:
        raise ValueError("at least one host/IP is required")
    if not token:
        raise ValueError("token is required")
    payload = {
        "type": PAYLOAD_TYPE,
        "version": 1,
        "ips": host_list,
        "port": int(port),
        "token": token,
        "protocol": protocol,
    }
    return json.dumps(payload, separators=(",", ":"))


def write_qr_png(payload: str, output_path: Path) -> Path:
    """Write a QR PNG. The payload contains a bearer token; protect the file."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install qrcode support with: pip install 'r1-hermes[qr]'") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = qrcode.make(payload)
    img.save(str(output_path))
    output_path.chmod(0o600)
    return output_path
