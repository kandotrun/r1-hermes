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

Status: runnable MVP. Do not expose directly to the public Internet.

## Install

```bash
git clone https://github.com/kandotrun/r1-hermes.git
cd r1-hermes
python -m pip install -e '.[qr]'
```

For development:

```bash
python -m pip install -e '.[dev,qr]'
```

## 1. Create a gateway token

The token is a bearer secret. Do not paste it into public chats or logs.

```bash
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
```

## 2. Run the Hermes-backed gateway

By default the gateway binds to localhost and calls `hermes chat --quiet` with the `safe` toolset.

```bash
r1-hermes hermes --host 127.0.0.1 --port 18789 --ready-file /tmp/r1-hermes.ready
```

Useful options:

```bash
r1-hermes hermes \
  --host 100.x.y.z \
  --port 18789 \
  --toolsets safe,web \
  --timeout 180
```

Use a Tailscale IP, firewall allowlist, or reverse proxy with mTLS or IP allowlisting when the Rabbit R1 must reach the gateway from another device. Do not use wildcard binds or expose the raw gateway directly to the public Internet.

## 3. Generate the Rabbit R1 QR payload

```bash
r1-hermes payload --host 100.x.y.z --port 18789 --protocol ws
```

To write a QR PNG:

```bash
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

The QR contains the bearer token. Treat the PNG as a secret and delete it after pairing. The
command creates the PNG as an owner-readable file, refuses to overwrite an existing output path
unless `--overwrite` is set, and does not print the payload JSON unless `--print-payload` is set.

## 4. Probe the running gateway before scanning with R1

The `probe` command simulates the Rabbit R1 WebSocket flow: challenge, connect, `chat.send`, and final chat event.

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --message 'Reply with OK from Hermes'
```

It prints only the assistant response; it does not print the issued device token.

## Revoke a paired device

If a Rabbit R1 device token or pairing QR may have been exposed, revoke the paired device before
issuing a fresh QR:

```bash
r1-hermes revoke --device-id r1-device-id
```

Then restart the device pairing flow with a newly generated gateway token and a new QR PNG. See
[`docs/security.md`](docs/security.md) for the full incident response checklist.

## Standalone demo gateway

```bash
r1-hermes serve --host 127.0.0.1 --port 18789
```

The demo handler echoes messages. Use `r1-hermes hermes` for a gateway that actually invokes Hermes Agent.

## Runtime behavior

- An R1 device must complete `connect` authentication before `chat.send` is accepted.
- Each device/session key resumes a stable Hermes CLI session via `hermes chat --continue r1-hermes-...`.
- Hermes stderr is not returned to R1 to avoid leaking secrets.
- Failures are returned as short, generic messages and details stay in local logs.

## systemd user service

For persistent operation, use the hardened user-service template in
[`packaging/systemd/r1-hermes.service`](packaging/systemd/r1-hermes.service) with an env file at
`~/.config/r1-hermes/r1-hermes.env`. The unit does not contain token literals and uses
`--ready-file` for health checks.

See [`docs/systemd-user-service.md`](docs/systemd-user-service.md) for install, enable, status,
logs, rollback, localhost, and Tailscale examples.

## Security

Read [`docs/security.md`](docs/security.md) before exposing the gateway to a network.
