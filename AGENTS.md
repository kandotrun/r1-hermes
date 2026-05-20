# Agent instructions for r1-hermes

Use this file when an autonomous coding/ops agent is handed this repository and asked to set up Rabbit R1 ↔ Hermes Agent pairing.

## Goal

Set up `r1-hermes`, run a Hermes-backed Rabbit R1/OpenClaw-compatible WebSocket gateway, verify it with the built-in probe, and generate a secret QR PNG that the human can scan from Rabbit R1.

## Security rules

- Treat `R1_HERMES_GATEWAY_TOKEN`, device tokens, QR payload JSON, and QR PNG files as secrets.
- Never paste full tokens or QR payloads into chat, issue comments, logs, PR descriptions, or screenshots.
- Bind to `127.0.0.1` for local smoke tests.
- For real Rabbit R1 pairing, prefer a narrow reachable address such as the host's Tailscale IP. Do not bind to `0.0.0.0` unless the human explicitly approves the network boundary.
- Default Hermes toolset should remain `safe`. Do not enable `terminal`, `file`, smart-home, or other high-impact toolsets for an R1 device unless the human explicitly approves.
- Delete the QR PNG after pairing unless the human asks to keep it.

## Canonical setup path

Follow `docs/agent-setup.md`. It contains the exact commands, verification steps, expected outputs, and troubleshooting notes.

## Fast path command outline

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,qr]'
hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with exactly OK'
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
r1-hermes hermes --host 127.0.0.1 --port 18789 --ready-file /tmp/r1-hermes.ready
```

In another terminal, probe it:

```bash
r1-hermes probe --url ws://127.0.0.1:18789/ --message 'Reply with exactly OK'
```

For actual R1 pairing, restart the gateway on the reachable host address and create a QR PNG:

```bash
r1-hermes hermes --host <REACHABLE_HOST_OR_TAILSCALE_IP> --port 18789
r1-hermes qr --host <REACHABLE_HOST_OR_TAILSCALE_IP> --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

Deliver the QR as a local file path or native media attachment only when the platform supports secure delivery. Remind the human that it contains a bearer secret.

## Verification before reporting success

- `python -m pytest -q` passes.
- `hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with exactly OK'` returns `OK` or an equivalent exact response.
- `r1-hermes probe --url ws://<host>:18789/ --message 'Reply with exactly OK'` returns a Hermes response and does not print the device token.
- The gateway is bound only to the intended host/port.
- The QR file exists, has restricted handling, and its path is reported without printing the underlying payload.

## Useful files

- `README.md` — user-facing setup overview.
- `docs/running.md` — concise running guide.
- `docs/agent-setup.md` — autonomous agent setup/runbook.
- `docs/security.md` — security checklist before network exposure.
- `src/r1_hermes/cli.py` — CLI commands: `hermes`, `serve`, `payload`, `qr`, `probe`.
- `tests/` — probe, parser, security, and CLI E2E tests.
