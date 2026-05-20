import pytest

from r1_hermes import cli


class FakeProbeClient:
    calls = []

    def __init__(self, *, url, token, device_id, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "token": token,
                "device_id": device_id,
                "timeout_seconds": timeout_seconds,
            }
        )

    async def send_message(self, message, *, session_key="main"):
        self.calls.append({"message": message, "session_key": session_key})
        return type(
            "ProbeResult",
            (),
            {"response_text": "probe ok", "run_id": "run-1", "device_token": "device-secret"},
        )()


def test_probe_command_sends_message_without_printing_device_token(monkeypatch, capsys):
    FakeProbeClient.calls = []
    monkeypatch.setattr(cli, "R1ProbeClient", FakeProbeClient)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "probe",
            "--url",
            "ws://127.0.0.1:18789/",
            "--device-id",
            "r1-test",
            "--session-key",
            "main",
            "--message",
            "hello",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "probe ok" in out
    assert "device-secret" not in out
    assert FakeProbeClient.calls == [
        {
            "url": "ws://127.0.0.1:18789/",
            "token": "gateway-secret",
            "device_id": "r1-test",
            "timeout_seconds": 30.0,
        },
        {"message": "hello", "session_key": "main"},
    ]


def test_probe_command_requires_token(monkeypatch):
    monkeypatch.delenv("R1_HERMES_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "probe", "--url", "ws://127.0.0.1:18789/", "--message", "hello"],
    )

    with pytest.raises(SystemExit, match="--token or R1_HERMES_GATEWAY_TOKEN is required"):
        cli.main()
