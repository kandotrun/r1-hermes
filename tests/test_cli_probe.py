import pytest

from r1_hermes import adapter as adapter_module
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
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT", "7")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT", "2")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS", "120")
    monkeypatch.setenv("R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS", "60")
    monkeypatch.setenv("R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS", "30")
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
    assert config.authenticated_connection_limit == 7
    assert config.authenticated_per_device_connection_limit == 2
    assert config.authenticated_idle_timeout_seconds == 15
    assert config.authenticated_max_lifetime_seconds == 120
    assert config.device_token_max_age_seconds == 60
    assert config.device_token_idle_timeout_seconds == 30


def test_server_command_reads_allowed_device_ids_from_env(monkeypatch, tmp_path):
    created = []

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            created.append({"config": config, "message_handler": message_handler})

    async def fake_run_forever(adapter, *, ready_file=None):
        created.append({"adapter": adapter, "ready_file": ready_file})

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_ALLOWED_DEVICE_IDS", "r1-env-a, r1-env-b\nr1-env-c")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "serve", "--state-dir", str(tmp_path)],
    )

    cli.main()

    config = created[0]["config"]
    assert config.allowed_device_ids == frozenset({"r1-env-a", "r1-env-b", "r1-env-c"})


def test_server_command_allows_repeatable_allowed_device_id_cli(monkeypatch, tmp_path):
    created = []

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            created.append({"config": config, "message_handler": message_handler})

    async def fake_run_forever(adapter, *, ready_file=None):
        created.append({"adapter": adapter, "ready_file": ready_file})

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_ALLOWED_DEVICE_IDS", "r1-env-ignored")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
            "--allowed-device-id",
            "r1-cli-a",
            "--allowed-device-id",
            "r1-cli-b",
        ],
    )

    cli.main()

    config = created[0]["config"]
    assert config.allowed_device_ids == frozenset({"r1-cli-a", "r1-cli-b"})


def test_server_command_reads_health_privacy_env(monkeypatch, tmp_path):
    created = []

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            created.append({"config": config, "message_handler": message_handler})

    async def fake_run_forever(adapter, *, ready_file=None):
        created.append({"adapter": adapter, "ready_file": ready_file})

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_ALLOW_REMOTE_HEALTH", "1")
    monkeypatch.setenv("R1_HERMES_HEALTH_DIAGNOSTICS", "1")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "serve", "--state-dir", str(tmp_path)],
    )

    cli.main()

    config = created[0]["config"]
    assert config.allow_remote_health is True
    assert config.health_diagnostics is True


def test_server_command_rejects_wildcard_bind_without_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--host",
            WILDCARD_IPV4,
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    error = str(exc_info.value)
    assert "Refusing wildcard bind host" in error
    assert "--allow-public-bind" in error
    assert "Tailscale" in error
    assert "reverse proxy with mTLS" in error
    assert "127.0.0.1" in error


def test_server_command_allows_wildcard_bind_with_explicit_flag(monkeypatch, tmp_path):
    captured = []

    async def fake_run_forever(adapter, *, ready_file=None):
        captured.append({"config": adapter.config, "ready_file": ready_file})

    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--host",
            WILDCARD_IPV4,
            "--allow-public-bind",
        ],
    )

    cli.main()

    assert captured[0]["config"].host == WILDCARD_IPV4
    assert captured[0]["config"].allow_public_bind is True


def test_server_command_allows_wildcard_bind_with_env_opt_in(monkeypatch, tmp_path):
    captured = []

    async def fake_run_forever(adapter, *, ready_file=None):
        captured.append(adapter.config)

    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_ALLOW_PUBLIC_BIND", "1")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--host",
            "::",
        ],
    )

    cli.main()

    assert captured[0].host == "::"
    assert captured[0].allow_public_bind is True


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
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = cli.DeviceState(tmp_path)
    state.issue_device_token("r1-test")

    now_ms = 1_001_000
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--device-id", "r1-test"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Revoked device: r1-test" in out
    assert "r1-test" not in cli.DeviceState(tmp_path).devices


def test_revoke_all_command_removes_all_devices(monkeypatch, capsys, tmp_path):
    state = cli.DeviceState(tmp_path)
    token_a = state.issue_device_token("r1-a")
    token_b = state.issue_device_token("r1-b")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--all"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Revoked 2 device(s): r1-a, r1-b" in out
    assert token_a not in out
    assert token_b not in out
    assert cli.DeviceState(tmp_path).devices == {}


def test_revoke_all_command_is_idempotent_with_empty_state(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--all"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "No paired devices found; state unchanged." in out
    assert cli.DeviceState(tmp_path).devices == {}


def test_revoke_all_dry_run_lists_device_ids_without_changing_state(
    monkeypatch, capsys, tmp_path
):
    state = cli.DeviceState(tmp_path)
    token_a = state.issue_device_token("r1-a")
    token_b = state.issue_device_token("r1-b")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--all", "--dry-run"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Would revoke 2 device(s): r1-a, r1-b" in out
    assert token_a not in out
    assert token_b not in out
    assert sorted(cli.DeviceState(tmp_path).devices) == ["r1-a", "r1-b"]


def test_revoke_command_fails_for_unknown_device(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "revoke", "--state-dir", str(tmp_path), "--device-id", "missing"],
    )

    with pytest.raises(SystemExit, match="device not found: missing"):
        cli.main()


def test_cleanup_command_prunes_expired_devices_without_printing_tokens(
    monkeypatch,
    capsys,
    tmp_path,
):
    now_ms = 1_000_000
    monkeypatch.setattr(adapter_module, "_now_ms", lambda: now_ms)
    state = cli.DeviceState(
        tmp_path,
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=0,
    )
    old_token = state.issue_device_token("r1-old")

    now_ms = 1_050_000
    fresh_token = state.issue_device_token("r1-fresh")

    now_ms = 1_061_000
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "cleanup",
            "--state-dir",
            str(tmp_path),
            "--device-token-max-age-seconds",
            "60",
            "--device-token-idle-timeout-seconds",
            "0",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Pruned expired devices: 1" in out
    assert old_token not in out
    assert fresh_token not in out
    remaining = cli.DeviceState(
        tmp_path,
        device_token_max_age_seconds=60,
        device_token_idle_timeout_seconds=0,
    ).devices
    assert set(remaining) == {"r1-fresh"}


def test_rotate_command_updates_env_file_and_revokes_all_without_printing_token(
    monkeypatch, capsys, tmp_path
):
    state = cli.DeviceState(tmp_path / "state")
    device_token = state.issue_device_token("r1-a")
    env_file = tmp_path / "r1-hermes.env"
    env_file.write_text(
        "R1_HERMES_GATEWAY_TOKEN=old-dummy-gateway-token\nR1_HERMES_HOST=127.0.0.1\n"
    )
    env_file.chmod(0o600)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _n: "new-dummy-gateway-token")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "rotate",
            "--state-dir",
            str(tmp_path / "state"),
            "--env-file",
            str(env_file),
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Rotated gateway token in" in out
    assert "Revoked 1 device(s): r1-a" in out
    assert "new-dummy-gateway-token" not in out
    assert device_token not in out
    assert "R1_HERMES_GATEWAY_TOKEN=new-dummy-gateway-token" in env_file.read_text()
    assert cli.DeviceState(tmp_path / "state").devices == {}


def test_rotate_command_requires_token_destination_without_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "rotate", "--state-dir", str(tmp_path)],
    )

    with pytest.raises(SystemExit, match="--env-file or --print-token is required"):
        cli.main()


def test_rotate_dry_run_does_not_update_env_or_state(monkeypatch, capsys, tmp_path):
    state = cli.DeviceState(tmp_path / "state")
    device_token = state.issue_device_token("r1-a")
    env_file = tmp_path / "r1-hermes.env"
    env_file.write_text("R1_HERMES_GATEWAY_TOKEN=old-dummy-gateway-token\n")
    env_file.chmod(0o600)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _n: "new-dummy-gateway-token")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "rotate",
            "--state-dir",
            str(tmp_path / "state"),
            "--env-file",
            str(env_file),
            "--dry-run",
        ],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "Would generate a new gateway token" in out
    assert "Would revoke 1 device(s): r1-a" in out
    assert "new-dummy-gateway-token" not in out
    assert device_token not in out
    assert "old-dummy-gateway-token" in env_file.read_text()
    assert sorted(cli.DeviceState(tmp_path / "state").devices) == ["r1-a"]


def test_rotate_print_token_is_explicitly_labeled(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _n: "new-dummy-gateway-token")
    monkeypatch.setattr(
        "sys.argv",
        ["r1-hermes", "rotate", "--state-dir", str(tmp_path), "--print-token"],
    )

    cli.main()

    out = capsys.readouterr().out
    assert "NEW R1_HERMES_GATEWAY_TOKEN (SECRET): new-dummy-gateway-token" in out
    assert "No paired devices found; state unchanged." in out


def test_serve_command_passes_concurrency_options(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--global-concurrency",
            "5",
            "--per-device-concurrency",
            "2",
            "--idempotency-cache-max-entries",
            "11",
            "--idempotency-cache-ttl-seconds",
            "22",
            "--authenticated-connection-limit",
            "4",
            "--authenticated-per-device-connection-limit",
            "1",
            "--authenticated-idle-timeout-seconds",
            "30",
            "--authenticated-max-lifetime-seconds",
            "600",
        ],
    )

    cli.main()

    assert captured["config"].global_concurrency == 5
    assert captured["config"].per_device_concurrency == 2
    assert captured["config"].idempotency_cache_max_entries == 11
    assert captured["config"].idempotency_cache_ttl_seconds == 22
    assert captured["config"].authenticated_connection_limit == 4
    assert captured["config"].authenticated_per_device_connection_limit == 1
    assert captured["config"].authenticated_idle_timeout_seconds == 30
    assert captured["config"].authenticated_max_lifetime_seconds == 600


def test_hermes_command_reads_concurrency_from_env(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_GLOBAL_CONCURRENCY", "6")
    monkeypatch.setenv("R1_HERMES_PER_DEVICE_CONCURRENCY", "3")
    monkeypatch.setenv("R1_HERMES_IDEMPOTENCY_CACHE_MAX_ENTRIES", "44")
    monkeypatch.setenv("R1_HERMES_IDEMPOTENCY_CACHE_TTL_SECONDS", "55")
    monkeypatch.setenv("R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS", "8")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT", "9")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT", "4")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS", "900")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
        ],
    )

    cli.main()

    assert captured["config"].global_concurrency == 6
    assert captured["config"].per_device_concurrency == 3
    assert captured["config"].idempotency_cache_max_entries == 44
    assert captured["config"].idempotency_cache_ttl_seconds == 55
    assert captured["config"].chat_run_timeout_seconds == 240
    assert captured["config"].chat_heartbeat_interval_seconds == 8
    assert captured["config"].authenticated_connection_limit == 9
    assert captured["config"].authenticated_per_device_connection_limit == 4
    assert captured["config"].authenticated_idle_timeout_seconds == 45
    assert captured["config"].authenticated_max_lifetime_seconds == 900
    assert isinstance(captured["message_handler"], cli.HermesCliRunner)
    assert captured["message_handler"].timeout_seconds == 240


def test_hermes_command_timeout_flag_configures_gateway_and_runner(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
            "--timeout",
            "90",
            "--heartbeat-interval",
            "3",
        ],
    )

    cli.main()

    assert captured["config"].chat_run_timeout_seconds == 90
    assert captured["config"].chat_heartbeat_interval_seconds == 3
    assert isinstance(captured["message_handler"], cli.HermesCliRunner)
    assert captured["message_handler"].timeout_seconds == 90


def test_hermes_command_allows_safe_and_web_toolsets(monkeypatch, tmp_path):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
            "--toolsets",
            "safe,web",
        ],
    )

    cli.main()

    assert isinstance(captured["message_handler"], cli.HermesCliRunner)
    assert captured["message_handler"].toolsets == "safe,web"
    assert captured["message_handler"].allow_high_impact_toolsets is False


def test_hermes_command_rejects_high_impact_toolsets_without_override(
    monkeypatch,
    tmp_path,
):
    created = []

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            created.append({"config": config, "message_handler": message_handler})

    async def fake_run_forever(adapter, *, ready_file=None):
        created.append({"adapter": adapter, "ready_file": ready_file})

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
            "--toolsets",
            "terminal,file",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    error = str(exc_info.value)
    assert "high-impact Hermes toolsets" in error
    assert "terminal" in error
    assert "file" in error
    assert "--allow-high-impact-toolsets" in error
    assert "gateway-secret" not in error
    assert created == []


def test_hermes_command_rejects_high_impact_toolsets_from_env_without_override(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_TOOLSETS", "terminal,file")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    error = str(exc_info.value)
    assert "high-impact Hermes toolsets" in error
    assert "terminal" in error
    assert "file" in error
    assert "gateway-secret" not in error


def test_hermes_command_allows_high_impact_toolsets_with_explicit_flag(
    monkeypatch,
    tmp_path,
):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
            "--toolsets",
            "terminal,file",
            "--allow-high-impact-toolsets",
        ],
    )

    cli.main()

    assert isinstance(captured["message_handler"], cli.HermesCliRunner)
    assert captured["message_handler"].toolsets == "terminal,file"
    assert captured["message_handler"].allow_high_impact_toolsets is True


def test_hermes_command_allows_high_impact_toolsets_with_env_override(
    monkeypatch,
    tmp_path,
):
    captured = {}

    class FakeAdapter:
        def __init__(self, config, *, message_handler):
            captured["config"] = config
            captured["message_handler"] = message_handler

    async def fake_run_forever(adapter, *, ready_file=None):
        captured["adapter"] = adapter
        captured["ready_file"] = ready_file

    monkeypatch.setattr(cli, "R1HermesAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "_run_forever", fake_run_forever)
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("R1_HERMES_TOOLSETS", "terminal,file")
    monkeypatch.setenv("R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS", "1")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "hermes",
            "--state-dir",
            str(tmp_path),
        ],
    )

    cli.main()

    assert isinstance(captured["message_handler"], cli.HermesCliRunner)
    assert captured["message_handler"].toolsets == "terminal,file"
    assert captured["message_handler"].allow_high_impact_toolsets is True


def test_server_command_reports_invalid_concurrency_without_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("R1_HERMES_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "sys.argv",
        [
            "r1-hermes",
            "serve",
            "--state-dir",
            str(tmp_path),
            "--global-concurrency",
            "0",
        ],
    )

    with pytest.raises(SystemExit, match="global_concurrency must be at least 1"):
        cli.main()
