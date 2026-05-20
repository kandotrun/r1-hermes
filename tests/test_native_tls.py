import asyncio
import ipaddress
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import ClientSession
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.mark.asyncio
async def test_cli_server_accepts_native_tls_and_serves_wss(tmp_path, unused_tcp_port):
    cert_file, key_file = write_self_signed_cert(tmp_path)
    ready_file = tmp_path / "ready"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    env["R1_HERMES_GATEWAY_TOKEN"] = "server-token"

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "r1_hermes.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(unused_tcp_port),
        "--state-dir",
        str(tmp_path / "state"),
        "--ready-file",
        str(ready_file),
        "--tls-cert-file",
        str(cert_file),
        "--tls-key-file",
        str(key_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        await wait_for_file(ready_file)
        async with ClientSession() as session:
            async with session.ws_connect(f"wss://127.0.0.1:{unused_tcp_port}/", ssl=False) as ws:
                challenge = await ws.receive_json(timeout=5)
                assert challenge["event"] == "connect.challenge"
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


@pytest.mark.asyncio
async def test_cli_server_requires_tls_cert_and_key_together(tmp_path, unused_tcp_port):
    cert_file, _key_file = write_self_signed_cert(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    env["R1_HERMES_GATEWAY_TOKEN"] = "server-token"

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "r1_hermes.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(unused_tcp_port),
        "--state-dir",
        str(tmp_path / "state"),
        "--tls-cert-file",
        str(cert_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)

    assert process.returncode != 0
    assert stdout.decode() == ""
    assert "--tls-cert-file and --tls-key-file must be provided together" in stderr.decode()
    assert "server-token" not in stderr.decode()


def write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_file = tmp_path / "server.key"
    cert_file = tmp_path / "server.crt"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_file, key_file


async def wait_for_file(path: Path, *, attempts: int = 50) -> None:
    for _ in range(attempts):
        if path.exists():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"ready file was not created: {path}")
