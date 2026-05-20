import pytest

from r1_hermes import cli


class FakeProbeClient:
    calls = []

    def __init__(self, *, url, token, device_id, timeout_seconds, connect_method="connect"):
        self.calls.append(
            {
                "url": url,
                "token": token,
                "device_id": device_id,
                "timeout_seconds": timeout_seconds,
                "connect_method": connect_method,
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
            "connect_method": "connect",
        },
        {"message": "hello", "session_key": "main"},
    ]


def test_probe_command_accepts_gateway_connect_variant(monkeypatch, capsys):
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
            "--connect-method",
            "gateway.connect",
            "--message",
            "hello",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "probe ok" in out
    assert FakeProbeClient.calls[0]["connect_method"] == "gateway.connect"


def test_probe_command_requires_token(monkeypatch):
    monkeypatch.delenv("R1_HERMES_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "probe", "--url", "ws://127.0.0.1:18789/", "--message", "hello"],
    )

    with pytest.raises(SystemExit, match="--token or R1_HERMES_GATEWAY_TOKEN is required"):
        cli.main()


def test_qr_command_does_not_print_payload_without_explicit_flag(monkeypatch, capsys, tmp_path):
    dummy_gateway_token = "gateway-secret"
    written = []

    def fake_write_qr_png(payload, output_path, *, overwrite=False):
        written.append({"payload": payload, "output_path": output_path, "overwrite": overwrite})
        return output_path

    monkeypatch.setattr(cli, "write_qr_png", fake_write_qr_png)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", dummy_gateway_token)
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "qr",
            "--host",
            "100.64.0.1",
            "--output",
            str(tmp_path / "pairing.png"),
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Wrote secret QR PNG:" in out
    assert dummy_gateway_token not in out
    assert '"token"' not in out
    assert written == [
        {
            "payload": (
                '{"type":"clawdbot-gateway","version":1,"ips":["100.64.0.1"],'
                f'"port":18789,"token":"{dummy_gateway_token}","protocol":"ws"}}'
            ),
            "output_path": tmp_path / "pairing.png",
            "overwrite": False,
        }
    ]


def test_qr_command_prints_payload_only_with_explicit_flag(monkeypatch, capsys, tmp_path):
    dummy_gateway_token = "gateway-secret"

    def fake_write_qr_png(payload, output_path, *, overwrite=False):
        return output_path

    monkeypatch.setattr(cli, "write_qr_png", fake_write_qr_png)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", dummy_gateway_token)
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "qr",
            "--host",
            "100.64.0.1",
            "--output",
            str(tmp_path / "pairing.png"),
            "--print-payload",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert f'"token":"{dummy_gateway_token}"' in out


def test_revoke_command_removes_device_from_state(monkeypatch, capsys, tmp_path):
    state = cli.DeviceState(tmp_path)
    state.issue_device_token("r1-test")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--device-id", "r1-test"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Revoked device: r1-test" in out
    assert "r1-test" not in cli.DeviceState(tmp_path).devices


def test_revoke_command_fails_for_unknown_device(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--device-id", "missing"],
    )

    with pytest.raises(SystemExit, match="device not found: missing"):
        cli.main()
