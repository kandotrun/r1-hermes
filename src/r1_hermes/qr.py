from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .token_policy import require_strong_gateway_token

PAYLOAD_TYPE = "clawdbot-gateway"
OWNER_READ_WRITE = 0o600
OWNER_READ_WRITE_EXECUTE = 0o700


def build_pairing_payload(
    *, hosts: Iterable[str], port: int, token: str, protocol: str = "ws"
) -> str:
    if protocol not in {"ws", "wss"}:
        raise ValueError("protocol must be 'ws' or 'wss'")
    host_list = [h.strip() for h in hosts if h and h.strip()]
    if not host_list:
        raise ValueError("at least one host/IP is required")
    require_strong_gateway_token(token, context="gateway token")
    payload = {
        "type": PAYLOAD_TYPE,
        "version": 1,
        "ips": host_list,
        "port": int(port),
        "token": token,
        "protocol": protocol,
    }
    return json.dumps(payload, separators=(",", ":"))


def write_qr_png(payload: str, output_path: Path, *, overwrite: bool = False) -> Path:
    """Write a QR PNG. The payload contains a bearer token; protect the file."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install qrcode support with: pip install 'r1-hermes[qr]'") from exc

    _mkdir_owner_only(output_path.parent)
    if output_path.exists() and not overwrite:
        raise FileExistsError(str(output_path))

    img = qrcode.make(payload)

    if overwrite:
        tmp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_READ_WRITE)
        try:
            with os.fdopen(fd, "wb") as handle:
                img.save(handle)
            tmp_path.chmod(OWNER_READ_WRITE)
            tmp_path.replace(output_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    else:
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_READ_WRITE)
        with os.fdopen(fd, "wb") as handle:
            img.save(handle)

    output_path.chmod(OWNER_READ_WRITE)
    return output_path


def _mkdir_owner_only(directory: Path) -> None:
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(mode=OWNER_READ_WRITE_EXECUTE)
        path.chmod(OWNER_READ_WRITE_EXECUTE)
