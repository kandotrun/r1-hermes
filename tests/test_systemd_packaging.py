from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "packaging" / "systemd" / "r1-hermes.service"
ENV_EXAMPLE = ROOT / "packaging" / "systemd" / "r1-hermes.env.example"
DOC = ROOT / "docs" / "systemd-user-service.md"
WILDCARD_HOST = ".".join(("0", "0", "0", "0"))


def test_systemd_service_uses_env_file_without_inline_token() -> None:
    service_text = SERVICE.read_text()

    assert "EnvironmentFile=%h/.config/r1-hermes/r1-hermes.env" in service_text
    assert "R1_HERMES_GATEWAY_TOKEN=" not in service_text
    assert "Environment=R1_HERMES_GATEWAY_TOKEN" not in service_text
    assert "Bearer " not in service_text
    assert WILDCARD_HOST not in service_text
    assert "--ready-file" in service_text


def test_env_example_keeps_localhost_default_and_token_placeholder() -> None:
    env_text = ENV_EXAMPLE.read_text()

    assert "R1_HERMES_HOST=127.0.0.1" in env_text
    assert "R1_HERMES_GATEWAY_TOKEN=replace-with-generated-token" in env_text
    assert "R1_HERMES_ALLOW_PUBLIC_BIND=1" in env_text
    assert WILDCARD_HOST not in env_text


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
        "r1-hermes probe",
        "systemctl --user enable --now r1-hermes.service",
        "journalctl --user-unit r1-hermes.service",
    ):
        assert required in doc_text
