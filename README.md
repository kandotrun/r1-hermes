# r1-hermes

Secure Rabbit R1 gateway adapter for Hermes Agent.

This repository is intentionally security-first. It implements the Rabbit R1/OpenClaw-compatible WebSocket handshake while avoiding the unsafe properties found in early proof-of-concept shims:

- no agent execution before successful authentication
- localhost bind by default
- no full-token logging
- no unauthenticated admin page
- device tokens are bound to device IDs and stored only as hashes
- unauthenticated handshake limits plus authenticated rate, length, global concurrency, and per-device concurrency limits
- explicit install docs and security checklist

Status: runnable MVP. The default runtime is a standalone bridge that accepts Rabbit R1/OpenClaw
WebSocket frames and invokes `hermes chat` for authenticated messages. A native Hermes Gateway
adapter path is prototyped in-library but is not wired into a released Hermes Gateway plugin yet.
Do not expose either mode directly to the public Internet.

## Agent handoff

If you hand this repository to Hermes Agent or another autonomous agent and want it to set up the gateway and produce a Rabbit R1 pairing QR, point it at [`AGENTS.md`](AGENTS.md) and [`docs/agent-setup.md`](docs/agent-setup.md). Those files contain the secret-safe setup runbook, probe commands, QR generation steps, and final report template.

## Install

Supported Python versions: Python 3.10 and newer. CI currently exercises Python 3.10,
3.11, and 3.12.

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
  --global-concurrency 2 \
  --per-device-concurrency 1 \
  --timeout 180
```

Use a Tailscale IP, firewall allowlist, or reverse proxy with mTLS or IP allowlisting when the Rabbit R1 must reach the gateway from another device. Do not use wildcard binds or expose the raw gateway directly to the public Internet.

`R1_HERMES_GLOBAL_CONCURRENCY` defaults to `2`, and `R1_HERMES_PER_DEVICE_CONCURRENCY` defaults
to `1`. A single-user Rabbit R1 deployment should usually keep those defaults. For a reviewed
multi-device deployment, raise the global cap only to the number of simultaneous Hermes subprocesses
the host can absorb, and keep the per-device cap low so one device cannot monopolize the gateway.
Requests over either cap receive a generic `BUSY` response before Hermes is invoked.

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

To verify the OpenClaw/Rabbit `gateway.connect` handshake variant, add
`--connect-method gateway.connect` to the probe command.

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

- An R1 device must complete `connect` or the compatible `gateway.connect` authentication before
  `chat.send` is accepted.
- Unauthenticated WebSocket clients are limited by peer IP before authentication. Repeated bad or
  malformed handshake attempts are closed with a policy-violation code and never reach Hermes.
- Each device/session key resumes a stable Hermes CLI session via `hermes chat --continue r1-hermes-...`.
- The gateway enforces global and per-device in-flight caps before starting `hermes chat`.
- Hermes stderr is not returned to R1 to avoid leaking secrets.
- Failures are returned as short, generic messages and details stay in local logs.

## Rate-limit configuration

The defaults are intentionally conservative for a localhost or private-network gateway:

```text
R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS=60
R1_HERMES_UNAUTHENTICATED_COOLDOWN_SECONDS=60
R1_HERMES_UNAUTHENTICATED_TIMEOUT_SECONDS=30
R1_HERMES_RATE_LIMIT_MESSAGES=12
R1_HERMES_RATE_LIMIT_WINDOW_SECONDS=60
```

Lower the unauthenticated limits for hostile networks. Do not loosen them to compensate for public
Internet exposure; use a narrow bind address, firewall, Tailscale, mTLS, or IP allowlisting instead.

## Hermes Gateway status

The `r1-hermes hermes` command is intentionally a subprocess bridge, not a native Hermes Gateway
platform adapter. It can pair an R1, authenticate `chat.send`, preserve a stable CLI session name,
and return final text replies. It does not currently participate in the same in-process gateway
message pipeline as Slack/Telegram adapters, so gateway-managed platform toolset resolution,
proactive delivery queues, media/STT/TTS handling, rich attachment mapping, and detailed gateway
session semantics remain out of scope for the default command.

For native Gateway work, `src/r1_hermes/native_gateway.py` contains a prototype adapter that
converts authenticated R1 `chat.send` requests into a small `MessageEvent`-compatible shape and
supports best-effort `send_text()` delivery to active WebSocket sessions. See
[`docs/running.md`](docs/running.md) for the integration options and migration notes.

## systemd user service

For persistent operation, use the hardened user-service template in
[`packaging/systemd/r1-hermes.service`](packaging/systemd/r1-hermes.service) with an env file at
`~/.config/r1-hermes/r1-hermes.env`. The unit does not contain token literals and uses
`--ready-file` for health checks.

See [`docs/systemd-user-service.md`](docs/systemd-user-service.md) for install, enable, status,
logs, rollback, localhost, and Tailscale examples.

## Security

Read [`docs/security.md`](docs/security.md) before exposing the gateway to a network.

Report suspected vulnerabilities privately. Use GitHub's private vulnerability reporting or
a draft GitHub Security Advisory when available; otherwise open a minimal issue asking for a
private maintainer contact without including exploit details, gateway tokens, device tokens,
QR payloads, API keys, or raw authorization headers. Public issues are fine for hardening
requests that do not disclose an active exploit or secret.
