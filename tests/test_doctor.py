import stat

import pytest

from r1_hermes import cli

WILDCARD_IPV4 = ".".join(("0", "0", "0", "0"))


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
                "frame_sink": frame_sink,
            }
        )

    async def send_message(self, message, *, session_key="main"):
        self.calls.append({"message": message, "session_key": session_key})
        return type(
            "ProbeResult",
            (),
            {
                "response_text": "OK with DUMMY_DEVICE_TOKEN_DO_NOT_USE",
                "device_token": "DUMMY_DEVICE_TOKEN_DO_NOT_USE",
                "run_id": "run-1",
            },
        )()


def test_doctor_reports_missing_gateway_token_without_leaking_values(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("R1_HERMES_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "R1_HERMES_GATEWAY_TOKEN" in out
    assert "missing" in out
    assert "token=" not in out


def test_doctor_fails_on_unsafe_state_dir_permissions(monkeypatch, capsys, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_dir.chmod(0o755)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(state_dir),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "state directory" in out
    assert "0755" in out
    assert "0700" in out


def test_doctor_fails_on_unsafe_state_files(monkeypatch, capsys, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    devices = state_dir / "devices.json"
    devices.write_text('{"devices":{}}')
    devices.chmod(0o644)
    key_file = state_dir / "device-token-hmac.key"
    key_file.write_text("00\n")
    key_file.chmod(0o600)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(state_dir),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "devices.json" in out
    assert "0644" in out
    assert "0600" in out
    assert "00\n" not in out


def test_doctor_rejects_state_dir_symlink(monkeypatch, capsys, tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    state_dir = tmp_path / "state-link"
    state_dir.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(state_dir),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "state directory" in out
    assert "symlink" in out


def test_doctor_rejects_wildcard_bind_without_opt_in(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path),
            "--host",
            WILDCARD_IPV4,
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "Refusing wildcard bind host" in out
    assert "--allow-public-bind" in out


def test_doctor_warns_on_wildcard_bind_with_explicit_opt_in(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path),
            "--host",
            WILDCARD_IPV4,
            "--allow-public-bind",
            "--skip-hermes-smoke",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "WARN" in out
    assert "wildcard bind acknowledged" in out


def test_doctor_detects_hermes_command_unavailable_without_shell(monkeypatch, capsys, tmp_path):
    calls = []

    async def fake_process_factory(*argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        raise FileNotFoundError("missing-hermes")

    monkeypatch.setattr(cli, "_create_subprocess_exec", fake_process_factory)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path),
            "--hermes-command",
            "missing-hermes",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Hermes CLI smoke" in out
    assert "FAIL" in out
    assert "not found" in out
    assert calls[0]["argv"][:4] == ("missing-hermes", "chat", "--quiet", "--source")
    assert calls[0]["kwargs"]["stdin"] == cli.asyncio.subprocess.DEVNULL


def test_doctor_redacts_token_probe_and_qr_values(monkeypatch, capsys, tmp_path):
    FakeProbeClient.calls = []
    qr_output = tmp_path / "r1-hermes-secret.png"

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"OK\n", b"DUMMY_GATEWAY_TOKEN_DO_NOT_USE stderr"

    async def fake_process_factory(*_argv, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(cli, "_create_subprocess_exec", fake_process_factory)
    monkeypatch.setattr(cli, "R1ProbeClient", FakeProbeClient)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path / "state"),
            "--host",
            "127.0.0.1",
            "--port",
            "18789",
            "--url",
            "ws://user:secret@example.test:18789/path?token=DUMMY_GATEWAY_TOKEN_DO_NOT_USE",
            "--qr-output",
            str(qr_output),
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "PASS" in out
    assert "WARN" in out
    assert "Summary: " in out
    assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in out
    assert "DUMMY_DEVICE_TOKEN_DO_NOT_USE" not in out
    assert "Reply with exactly OK" not in out
    assert "user:secret" not in out
    assert "token=" not in out
    assert "/path" not in out
    assert str(qr_output) in out
    assert FakeProbeClient.calls[0]["token"] == "DUMMY_GATEWAY_TOKEN_DO_NOT_USE"
    assert FakeProbeClient.calls[1]["message"] == "Reply with exactly OK"


def test_doctor_exits_zero_when_only_warnings_are_present(monkeypatch, capsys, tmp_path):
    state_dir = tmp_path / "state"
    qr_output = tmp_path / "existing.png"
    qr_output.write_bytes(b"secret qr placeholder")
    qr_output.chmod(0o600)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(state_dir),
            "--host",
            "127.0.0.1",
            "--qr-output",
            str(qr_output),
            "--skip-hermes-smoke",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "WARN" in out
    assert "already exists" in out
    assert stat.S_IMODE(qr_output.stat().st_mode) == 0o600


def test_doctor_fails_on_unsafe_existing_qr_output(monkeypatch, capsys, tmp_path):
    qr_output = tmp_path / "existing.png"
    qr_output.write_bytes(b"secret qr placeholder")
    qr_output.chmod(0o644)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path / "state"),
            "--qr-output",
            str(qr_output),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "QR output path" in out
    assert "0644" in out
    assert "0600" in out


def test_doctor_rejects_qr_output_symlink(monkeypatch, capsys, tmp_path):
    target = tmp_path / "target.png"
    target.write_bytes(b"placeholder")
    qr_output = tmp_path / "qr-link.png"
    qr_output.symlink_to(target)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path / "state"),
            "--qr-output",
            str(qr_output),
            "--skip-hermes-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "QR output path" in out
    assert "symlink" in out


def test_doctor_fails_when_probe_fails_without_echoing_exception_detail(
    monkeypatch,
    capsys,
    tmp_path,
):
    class FailingProbeClient(FakeProbeClient):
        async def send_message(self, message, *, session_key="main"):
            raise RuntimeError("DUMMY_GATEWAY_TOKEN_DO_NOT_USE raw prompt Reply with exactly OK")

    async def fake_process_factory(*_argv, **_kwargs):
        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"OK\n", b""

        return FakeProcess()

    monkeypatch.setattr(cli, "_create_subprocess_exec", fake_process_factory)
    monkeypatch.setattr(cli, "R1ProbeClient", FailingProbeClient)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "DUMMY_GATEWAY_TOKEN_DO_NOT_USE")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "doctor",
            "--state-dir",
            str(tmp_path / "state"),
            "--url",
            "ws://127.0.0.1:18789/",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Gateway probe" in out
    assert "RuntimeError" in out
    assert "DUMMY_GATEWAY_TOKEN_DO_NOT_USE" not in out
    assert "Reply with exactly OK" not in out
