import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
RUNNING = ROOT / "docs" / "running.md"
SECURITY = ROOT / "docs" / "security.md"
SYSTEMD = ROOT / "docs" / "systemd-user-service.md"
PUBLIC_WSS = ROOT / "docs" / "public-wss-native-tls.md"
TARGET_DOCS = (README, RUNNING, SECURITY, SYSTEMD, PUBLIC_WSS)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fenced_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def test_running_docs_have_private_proxy_deployment_recipes() -> None:
    running = _read(RUNNING)

    for required in (
        "Tailscale Serve recipe",
        "--host 127.0.0.1",
        "--port 18789",
        "tailscale serve --bg --https=443 127.0.0.1:18789",
        "tailscale serve status",
        "ss -ltnp",
        "wss://r1-hermes-host.tailnet-name.ts.net/",
        "--url wss://r1-hermes-host.tailnet-name.ts.net/",
        "Reverse proxy recipe",
        "client_auth",
        "trust_pool file /etc/caddy/r1-client-ca.pem",
        "remote_ip 198.51.100.10 203.0.113.0/24 2001:db8:1234::/48",
        "reverse_proxy 127.0.0.1:18789",
        "respond @not_allowed \"forbidden\" 403",
        "must not return HTTP 200",
    ):
        assert required in running


def test_qr_protocol_guidance_is_repeated_in_user_facing_docs() -> None:
    for doc in (README, RUNNING, SECURITY, SYSTEMD):
        text = _read(doc)
        assert "Use `ws://`" in text
        assert "Use `wss://`" in text
        assert "Do not mark a plain `ws://` backend as" in text
        assert "`wss://`" in text


def test_deployment_examples_do_not_print_qr_payloads_or_tokens() -> None:
    combined = "\n".join(_read(doc) for doc in TARGET_DOCS)

    for block in _fenced_blocks(combined):
        assert "--print-payload" not in block
        assert "R1_HERMES_GATEWAY_TOKEN=old-dummy" not in block
        assert "Authorization: Bearer" not in block


def test_docs_cover_r1_timeout_and_heartbeat_policy() -> None:
    combined = "\n".join(_read(doc) for doc in TARGET_DOCS)

    for required in (
        "R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS",
        "R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS",
        "CHAT_RUN_TIMEOUT",
        "exceeded the R1 gateway timeout limit",
        "--heartbeat-interval",
        "tool stderr",
        "prompts",
    ):
        assert required in combined


def test_systemd_docs_cover_tailscale_serve_and_proxy_checks() -> None:
    systemd = _read(SYSTEMD)

    for required in (
        "Tailscale Serve with localhost bind",
        "R1_HERMES_HOST=127.0.0.1",
        "tailscale serve --bg --https=443 127.0.0.1:18789",
        "Reverse proxy with localhost bind",
        "Caddyfile",
        "r1-hermes probe --url wss://r1.example.com/",
        "From an untrusted network",
    ):
        assert required in systemd


def test_public_wss_native_tls_runbook_covers_security_acceptance_criteria() -> None:
    runbook = _read(PUBLIC_WSS)

    for required in (
        "Public native WSS runbook",
        "Prefer Tailscale or private networking",
        "sslip.io",
        "custom domain",
        "Let's Encrypt",
        "certbot certonly --standalone",
        "R1_HERMES_TLS_CERT_FILE",
        "R1_HERMES_TLS_KEY_FILE",
        "chmod 640",
        "chgrp r1-hermes-certs",
        "R1_HERMES_ALLOWED_DEVICE_IDS",
        "locked-down steady-state",
        "private first-pairing boundary",
        "do not run first-pairing without an allowlist on this public boundary",
        "Do not paste raw device IDs",
        "ProtectHome=read-only",
        "ReadWritePaths=",
        "systemd-analyze --user verify",
        "certbot renew --dry-run",
        "openssl x509 -checkend",
        "r1-hermes probe --url wss://",
        "Rollback",
        "shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png",
    ):
        assert required in runbook

    forbidden_examples = (
        "--print-payload",
        "Authorization: Bearer",
        "R1_HERMES_GATEWAY_TOKEN=",
        "r1-real-device-id",
    )
    for block in _fenced_blocks(runbook):
        for forbidden in forbidden_examples:
            assert forbidden not in block


def test_user_facing_docs_link_public_wss_runbook() -> None:
    for doc in (README, RUNNING, SECURITY, SYSTEMD):
        assert "public-wss-native-tls.md" in _read(doc)


def test_relative_markdown_links_resolve() -> None:
    docs = (README, *sorted((ROOT / "docs").glob("*.md")))
    link_pattern = re.compile(r"!?!\[[^\]]+\]\(([^)]+)\)")

    for doc in docs:
        text = _read(doc)
        for match in link_pattern.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            assert (doc.parent / target).resolve().exists(), f"broken link in {doc}: {target}"
