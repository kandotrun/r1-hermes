import os
import sys
import textwrap
from pathlib import Path

import pytest

from r1_hermes.r1_client import R1ProbeClient

from .replay_helpers import FixtureReplayFlow, replay_fixture_flow

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "r1_payloads"


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
    env["PYTHONPATH"] = str(Path.cwd() / "src")
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

        replay = await replay_fixture_flow(
            url=f"ws://127.0.0.1:{port}/",
            fixture_dir=FIXTURE_DIR,
            flow=FixtureReplayFlow(
                connect_fixture="gateway_connect_community_shim.json",
                chat_fixture="community_shim_chat_message_object.json",
                expected_device_id="r1-community-shim",
                expected_message="hello Hermes from community shim fixture",
                expected_session_key="community-main",
                expected_run_id="community-run-001",
                expected_ack_events=("connect.ok", "node.pair.approved"),
            ),
            gateway_token="server-token",
        )

        assert replay.response_text == "FAKE HERMES: hello Hermes from community shim fixture"
    finally:
        if process.returncode is None:
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
