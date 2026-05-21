import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import ClientSession

from .token_fixtures import STRONG_GATEWAY_TOKEN


@pytest.mark.asyncio
async def test_cli_server_accepts_native_tls_and_serves_wss(tmp_path, unused_tcp_port):
    cert_file, key_file = write_self_signed_cert(tmp_path)
    ready_file = tmp_path / "ready"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd() / "src")
    env["R1_HERMES_GATEWAY_TOKEN"] = STRONG_GATEWAY_TOKEN

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
    env["R1_HERMES_GATEWAY_TOKEN"] = STRONG_GATEWAY_TOKEN

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
    assert STRONG_GATEWAY_TOKEN not in stderr.decode()


def write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    key_file = tmp_path / "server.key"
    cert_file = tmp_path / "server.crt"
    openssl = shutil.which("openssl")
    assert openssl is not None
    subprocess.run(  # noqa: S603 - test-only invocation with fixed arguments and temporary paths
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    key_file.chmod(0o600)
    return cert_file, key_file


async def wait_for_file(path: Path, *, attempts: int = 50) -> None:
    for _ in range(attempts):
        if path.exists():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"ready file was not created: {path}")
