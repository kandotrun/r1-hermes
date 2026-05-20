import os
import sys
import textwrap

import pytest

from r1_hermes.r1_client import R1ProbeClient


@pytest.mark.asyncio
async def test_hermes_cli_server_runs_with_fake_hermes_and_probe(tmp_path, unused_tcp_port):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_hermes = fake_bin / "hermes"
    fake_hermes.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import sys
            query = sys.argv[sys.argv.index('--query') + 1]
            print('FAKE HERMES: ' + query)
            """
        ).lstrip()
    )
    fake_hermes.chmod(0o700)

    ready_file = tmp_path / "ready"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["R1_HERMES_GATEWAY_TOKEN"] = "server-token"
    port = unused_tcp_port
    process = await asyncio_subprocess_exec(
        sys.executable,
        "-m",
        "r1_hermes.cli",
        "hermes",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ready-file",
        str(ready_file),
        "--timeout",
        "5",
        env=env,
    )
    try:
        await wait_for_file(ready_file)
        result = await R1ProbeClient(
            url=f"ws://127.0.0.1:{port}/",
            token="server-token",
            device_id="r1-e2e",
            timeout_seconds=5,
        ).send_message("hello from probe")

        assert result.response_text == "FAKE HERMES: hello from probe"
    finally:
        process.terminate()
        await process.wait()


async def asyncio_subprocess_exec(*args, **kwargs):
    import asyncio

    return await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )


async def wait_for_file(path, *, attempts=50):
    import asyncio

    for _ in range(attempts):
        if path.exists():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"ready file was not created: {path}")
