# r1-hermes

Secure Rabbit R1 gateway adapter for Hermes Agent.

This repository is intentionally security-first. It implements the Rabbit R1/OpenClaw-compatible WebSocket handshake while avoiding the unsafe properties found in early proof-of-concept shims:

- no agent execution before successful authentication
- localhost bind by default
- no full-token logging
- no unauthenticated admin page
- device tokens are bound to device IDs and stored only as hashes
- rate limits, message length limits, and per-device concurrency limits
- explicit install docs and security checklist

Status: early development. Do not expose directly to the public Internet.

## Install for development

```bash
git clone https://github.com/kandotrun/r1-hermes.git
cd r1-hermes
python -m pip install -e '.[dev,qr]'
```

## Generate a pairing payload

The payload contains a bearer secret. Do not paste it into public chats or logs.

```bash
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
r1-hermes payload --host 100.x.y.z --port 18789 --protocol ws
```

To write a QR PNG:

```bash
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

## Run standalone demo gateway

```bash
export R1_HERMES_GATEWAY_TOKEN="..."
r1-hermes serve --host 127.0.0.1 --port 18789
```

This demo handler echoes messages. The intended next step is a Hermes Gateway platform integration that passes authenticated `chat.send` frames into Hermes with a minimal, explicit toolset.

## Security

Read [`docs/security.md`](docs/security.md) before exposing the gateway to a network.
