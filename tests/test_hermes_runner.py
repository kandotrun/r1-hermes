import asyncio

import pytest

from r1_hermes.hermes_runner import HermesCliRunner, build_session_name


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, _stdin=None):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_runner_invokes_hermes_chat_with_safe_defaults():
    calls = []

    async def fake_factory(*argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return FakeProcess(b"hello from hermes\n")

    runner = HermesCliRunner(process_factory=fake_factory, timeout_seconds=3)
    response = await runner("hi r1", device_id="rabbit:one", session_key="main")

    assert response == "hello from hermes"
    assert calls[0]["argv"] == (
        "hermes",
        "chat",
        "--quiet",
        "--source",
        "r1-hermes",
        "--toolsets",
        "safe",
        "--continue",
        build_session_name("rabbit:one", "main"),
        "--query",
        "hi r1",
    )
    assert calls[0]["kwargs"]["stdin"] == asyncio.subprocess.DEVNULL
    assert calls[0]["kwargs"]["stdout"] == asyncio.subprocess.PIPE
    assert calls[0]["kwargs"]["stderr"] == asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_runner_can_disable_session_continuation_and_set_toolsets():
    calls = []

    async def fake_factory(*argv, **kwargs):
        calls.append(argv)
        return FakeProcess(b"ok")

    runner = HermesCliRunner(
        command=("/usr/local/bin/hermes",),
        toolsets="safe,web",
        continue_sessions=False,
        process_factory=fake_factory,
    )
    assert await runner("question", device_id="r1", session_key="s") == "ok"

    assert calls[0] == (
        "/usr/local/bin/hermes",
        "chat",
        "--quiet",
        "--source",
        "r1-hermes",
        "--toolsets",
        "safe,web",
        "--query",
        "question",
    )


@pytest.mark.asyncio
async def test_runner_returns_short_error_without_leaking_stderr_on_failure():
    async def fake_factory(*_argv, **_kwargs):
        return FakeProcess(b"", b"SECRET_TOKEN=abc failure details", 2)

    runner = HermesCliRunner(process_factory=fake_factory)
    response = await runner("hi", device_id="r1", session_key="main")

    assert response == "Hermes command failed with exit code 2. Check r1-hermes logs."
    assert "SECRET_TOKEN" not in response


@pytest.mark.asyncio
async def test_runner_times_out_cleanly():
    class HangingProcess:
        returncode = None

        async def communicate(self, _stdin=None):
            await asyncio.sleep(60)
            return b"late", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            return 1

    process = HangingProcess()

    async def fake_factory(*_argv, **_kwargs):
        return process

    runner = HermesCliRunner(process_factory=fake_factory, timeout_seconds=0.01)
    response = await runner("hi", device_id="r1", session_key="main")

    assert response == "Hermes command timed out. Please try again with a shorter request."
    assert process.killed is True


def test_session_name_is_stable_and_shell_safe():
    first = build_session_name("rabbit:one/../../x", "main chat")
    second = build_session_name("rabbit:one/../../x", "main chat")

    assert first == second
    assert first.startswith("r1-hermes-")
    assert all(ch.isalnum() or ch in "-_" for ch in first)
    assert len(first) <= 80
