import pytest

from r1_hermes import cli


class FakeProbeClient:
    calls = []

    def __init__(
        self,
        *,
        url,
        token,
        device_id,
        timeout_seconds,
        connect_method="connect",
        dump_frames=False,
        frame_sink=None,
    ):
        self.calls.append(
            {
                "url": url,
                "token": token,
                "device_id": device_id,
                "timeout_seconds": timeout_seconds,
                "connect_method": connect_method,
                "dump_frames": dump_frames,
                "frame_sink": frame_sink is not None,
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
            "dump_frames": False,
            "frame_sink": True,
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


def test_probe_command_enables_safe_frame_dump(monkeypatch, capsys):
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
            "--dump-frames",
            "--message",
            "hello",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "probe ok" in out
    assert FakeProbeClient.calls[0]["dump_frames"] is True
    assert FakeProbeClient.calls[0]["frame_sink"] is True


def test_probe_command_requires_token(monkeypatch):
    monkeypatch.delenv("R1_HERMES_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "probe", "--url", "ws://127.0.0.1:18789/", "--message", "hello"],
    )

    with pytest.raises(SystemExit, match="--token or R1_HERMES_GATEWAY_TOKEN is required"):
        cli.main()


def test_server_command_reads_unauthenticated_limit_env(monkeypatch, tmp_path):
    created = []

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            created.append({"config": config, "message_handler": message_handler})

    async def fake_run_forever(adapter, *, ready_file=None):
        created.append({"adapter": adapter, "ready_file": ready_file})

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT", "3")
    monkeypatch.setenv("R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT", "4")
    monkeypatch.setenv("R1_HERMES_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS", "5")
    monkeypatch.setenv("R1_HERMES_UNAUTHENTICATED_COOLDOWN_SECONDS", "6")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            "18789",
        ],
    )

    cli.main()

    config = created[0]["config"]
    assert config.gateway_token == "gateway-secret"
    assert config.host == "127.0.0.1"
    assert config.port == 18789
    assert config.unauthenticated_connection_limit == 3
    assert config.unauthenticated_attempt_limit == 4
    assert config.unauthenticated_attempt_window_seconds == 5
    assert config.unauthenticated_cooldown_seconds == 6


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
