from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "packaging" / "systemd" / "r1-hermes.service"
ENV_EXAMPLE = ROOT / "packaging" / "systemd" / "r1-hermes.env.example"
PACKAGE_SYSTEMD = ROOT / "src" / "r1_hermes" / "systemd"
DOC = ROOT / "docs" / "systemd-user-service.md"
WILDCARD_HOST = ".".join(("0", "0", "0", "0"))


def _pythonpath_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def test_systemd_service_uses_env_file_without_inline_token() -> None:
    service_text = SERVICE.read_text()

    assert "EnvironmentFile=%h/.config/r1-hermes/r1-hermes.env" in service_text
    assert "R1_HERMES_GATEWAY_TOKEN=" not in service_text
    assert "Environment=R1_HERMES_GATEWAY_TOKEN" not in service_text
    assert "Bearer " not in service_text
    assert WILDCARD_HOST not in service_text
    assert "--ready-file" in service_text


def test_systemd_service_limits_writable_paths() -> None:
    service_text = SERVICE.read_text()

    assert "WorkingDirectory=%S/r1-hermes" in service_text
    assert "WorkingDirectory=%h" not in service_text
    assert "StateDirectory=r1-hermes" in service_text
    assert "StateDirectoryMode=0700" in service_text
    assert "RuntimeDirectory=r1-hermes" in service_text
    assert "RuntimeDirectoryMode=0700" in service_text
    assert "ProtectSystem=strict" in service_text
    assert "ProtectHome=tmpfs" in service_text
    assert "BindReadOnlyPaths=%h/.local/bin %h/.local/lib" in service_text
    assert "BindPaths=%S/r1-hermes %t/r1-hermes -%h/.hermes" in service_text
    assert "RestrictNamespaces=true" in service_text
    assert "RestrictRealtime=true" in service_text
    assert "ProtectHostname=true" in service_text
    assert "SystemCallFilter=@system-service" in service_text
    assert "SystemCallFilter=~@privileged @resources" in service_text
    assert "--state-dir %S/r1-hermes" in service_text
    assert "--ready-file %t/r1-hermes/ready" in service_text
    assert "--timeout ${R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS}" in service_text
    assert "--heartbeat-interval ${R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS}" in service_text
    assert "--outbound-text-max-chars ${R1_HERMES_OUTBOUND_TEXT_MAX_CHARS}" in service_text
    assert "--outbound-event-max-bytes ${R1_HERMES_OUTBOUND_EVENT_MAX_BYTES}" in service_text


def test_env_example_keeps_localhost_default_and_token_placeholder() -> None:
    env_text = ENV_EXAMPLE.read_text()

    assert "R1_HERMES_HOST=127.0.0.1" in env_text
    assert "R1_HERMES_GATEWAY_TOKEN=replace-with-generated-token" in env_text
    assert "# R1_HERMES_ALLOWED_DEVICE_IDS=<INTENDED_R1_DEVICE_ID>" in env_text
    assert "private first-pairing" in env_text
    assert "locked-down steady-state" in env_text
    assert "Do not paste real device IDs into issues, logs, docs, or support tickets." in env_text
    assert "R1_HERMES_ALLOW_PUBLIC_BIND=1" in env_text
    assert "R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1" in env_text
    assert "R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS=180" in env_text
    assert "R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS=15" in env_text
    assert "R1_HERMES_OUTBOUND_TEXT_MAX_CHARS=8192" in env_text
    assert "R1_HERMES_OUTBOUND_EVENT_MAX_BYTES=65536" in env_text
    assert "# R1_HERMES_ALLOW_REMOTE_HEALTH=1" in env_text
    assert "# R1_HERMES_HEALTH_DIAGNOSTICS=1" in env_text
    assert WILDCARD_HOST not in env_text
    assert "r1-real-device-id" not in env_text


def test_packaged_systemd_assets_match_source_templates() -> None:
    assert (PACKAGE_SYSTEMD / "r1-hermes.service").read_text() == SERVICE.read_text()
    assert (PACKAGE_SYSTEMD / "r1-hermes.env.example").read_text() == ENV_EXAMPLE.read_text()


def test_install_systemd_user_helper_writes_templates_without_secrets(tmp_path: Path) -> None:
    unit_output = tmp_path / "config" / "systemd" / "user" / "r1-hermes.service"
    env_output = tmp_path / "config" / "r1-hermes" / "r1-hermes.env"

    result = subprocess.run(  # noqa: S603 - trusted CLI invocation in the test interpreter.
        [
            sys.executable,
            "-m",
            "r1_hermes.cli",
            "install-systemd-user",
            "--unit-output",
            str(unit_output),
            "--env-output",
            str(env_output),
        ],
        cwd=ROOT,
        env=_pythonpath_env(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert unit_output.read_text() == SERVICE.read_text()
    assert env_output.read_text() == ENV_EXAMPLE.read_text()
    assert stat.S_IMODE(env_output.stat().st_mode) == 0o600
    assert stat.S_IMODE(unit_output.stat().st_mode) == 0o644
    assert "R1_HERMES_GATEWAY_TOKEN=" not in result.stdout
    assert "replace-with-generated-token" not in result.stdout
    assert str(unit_output) in result.stdout
    assert str(env_output) in result.stdout


def test_install_systemd_user_helper_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    unit_output = tmp_path / "r1-hermes.service"
    env_output = tmp_path / "r1-hermes.env"
    unit_output.write_text("existing unit\n")
    env_output.write_text("existing env\n")

    result = subprocess.run(  # noqa: S603 - trusted CLI invocation in the test interpreter.
        [
            sys.executable,
            "-m",
            "r1_hermes.cli",
            "install-systemd-user",
            "--unit-output",
            str(unit_output),
            "--env-output",
            str(env_output),
        ],
        cwd=ROOT,
        env=_pythonpath_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert unit_output.read_text() == "existing unit\n"
    assert env_output.read_text() == "existing env\n"


def test_install_systemd_user_helper_preflights_both_outputs(tmp_path: Path) -> None:
    unit_output = tmp_path / "r1-hermes.service"
    env_output = tmp_path / "r1-hermes.env"
    env_output.write_text("existing env\n")

    result = subprocess.run(  # noqa: S603 - trusted CLI invocation in the test interpreter.
        [
            sys.executable,
            "-m",
            "r1_hermes.cli",
            "install-systemd-user",
            "--unit-output",
            str(unit_output),
            "--env-output",
            str(env_output),
        ],
        cwd=ROOT,
        env=_pythonpath_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert not unit_output.exists()
    assert env_output.read_text() == "existing env\n"


def test_systemd_docs_cover_operations_and_health_checks() -> None:
    doc_text = DOC.read_text()

    for required in (
        "Install",
        "Enable",
        "Status",
        "Logs",
        "Rollback",
        "127.0.0.1",
        "100.x.y.z",
        "--ready-file",
        "curl --fail --silent http://127.0.0.1:18789/healthz",
        '{"ok": true}',
        "R1_HERMES_ALLOW_REMOTE_HEALTH=1",
        "R1_HERMES_HEALTH_DIAGNOSTICS=1",
        "r1-hermes probe",
        "R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS",
        "R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS",
        "R1_HERMES_OUTBOUND_TEXT_MAX_CHARS",
        "R1_HERMES_OUTBOUND_EVENT_MAX_BYTES",
        "CHAT_OUTPUT_TOO_LARGE",
        "systemctl --user enable --now r1-hermes.service",
        "journalctl --user-unit r1-hermes.service",
        "## Write-path assumptions",
        "%S/r1-hermes",
        "%t/r1-hermes/ready",
        "%h/.hermes",
        "ProtectHome=tmpfs",
        "BindReadOnlyPaths",
        "BindPaths",
        "ReadWritePaths",
        "## Permission-related startup failures",
        "systemd-analyze --user verify",
    ):
        assert required in doc_text
