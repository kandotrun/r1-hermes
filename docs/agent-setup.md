# Autonomous agent setup guide

This guide is written for Hermes Agent, Codex, Claude Code, OpenCode, or another autonomous agent that has been handed this repository and asked to make Rabbit R1 talk to Hermes Agent.

Your job is to install this repo, verify Hermes is callable, start the R1-compatible gateway, probe the wire flow, and produce a QR PNG for the human to scan. Keep secrets out of chat/logs.

## 0. What this repo does

`r1-hermes` is a hardened Rabbit R1/OpenClaw-compatible WebSocket gateway. It receives Rabbit R1 chat messages and forwards them to Hermes Agent through `hermes chat`.

The expected flow is:

1. Agent installs this Python package.
2. Agent creates a fresh `R1_HERMES_GATEWAY_TOKEN` bearer secret.
3. Agent starts `r1-hermes hermes` on a reachable host/port.
4. Agent verifies the gateway with `r1-hermes probe`.
5. Agent generates `r1-hermes-secret.png` with `r1-hermes qr`.
6. Human scans the QR from Rabbit R1.
7. Agent deletes the QR after pairing unless told otherwise.

## 1. Non-negotiable safety rules

- Never print the full token, QR payload JSON, `deviceToken`, or QR image contents.
- Never paste the QR payload into GitHub issues, PRs, Slack, logs, or final summaries.
- Only report short status messages and the QR file path/attachment.
- Default to `--host 127.0.0.1` for local tests.
- For real device pairing, use the narrowest reachable address. Tailscale IP is preferred.
- Avoid `--host 0.0.0.0` and `--host ::`. These wildcard binds fail closed unless
  `--allow-public-bind` or `R1_HERMES_ALLOW_PUBLIC_BIND=1` is set. If you think that opt-in is
  required, stop and ask the human to approve the network boundary.
- Default to `--toolsets safe`.
- Do not enable `terminal`, `file`, browser automation, home automation, vehicle controls, or other high-impact toolsets for R1 unless explicitly approved.

## 2. Prerequisites

Check these before running the gateway:

```bash
python --version
python -m pip --version
hermes --version
hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with exactly OK'
```

Expected Hermes smoke-test output should be exactly or effectively:

```text
OK
```

If `hermes` is missing, ask the human whether to install Hermes Agent. Do not invent a fake Hermes binary.

## 3. Install this repo

From the repository root, prefer a project-local virtual environment. This avoids system Python `externally-managed-environment` failures and prevents an old editable install from another workspace from shadowing this checkout.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,qr]'
python - <<'PY'
import r1_hermes
print(r1_hermes.__file__)
PY
r1-hermes --help
python -m pytest -q
```

The printed `r1_hermes.__file__` path must point inside this repository, not a temporary agent workspace. Expected test result:

```text
... passed ...
```

If tests fail, fix code/tests using the repo conventions before pairing a real device.

## 4. Create a fresh gateway token

Create a fresh bearer token for this pairing session:

```bash
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
```

Do not echo this environment variable. If you accidentally expose it, rotate it by generating a new one and restart the gateway.

## 5. Local smoke test

Start a localhost gateway in one terminal:

```bash
r1-hermes hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --toolsets safe \
  --global-concurrency 2 \
  --per-device-concurrency 1 \
  --ready-file /tmp/r1-hermes.ready
```

Expected stdout includes:

```text
r1-hermes listening on 127.0.0.1:18789
```

In another terminal with the same `R1_HERMES_GATEWAY_TOKEN` environment variable, probe it:

```bash
r1-hermes probe \
  --url ws://127.0.0.1:18789/ \
  --message 'Reply with exactly OK'
```

Expected output is a Hermes response such as:

```text
OK
```

The probe must not print `deviceToken` or the gateway token.

## 6. Choose the real pairing address

Rabbit R1 must be able to reach the host and port in the QR payload. Choose one:

- Tailscale IP, preferred: `100.x.y.z`
- LAN IP, only if the host and R1 are on the same trusted LAN
- Reverse proxy with `wss`, only if TLS and auth boundaries are intentionally configured

Discover candidate addresses without exposing secrets:

```bash
hostname -I
command -v tailscale >/dev/null && tailscale ip -4 || true
```

Pick the narrowest address that Rabbit R1 can reach. Use examples below with `<REACHABLE_HOST>` replaced by that address.

Do not substitute `0.0.0.0` or `::` for `<REACHABLE_HOST>`. The gateway rejects wildcard binds
without an explicit public-bind acknowledgement, and QR payloads must advertise a concrete address
that Rabbit R1 can actually reach. Prefer localhost plus Tailscale Serve, a reverse proxy with mTLS,
or a specific Tailscale/LAN IP.

## 7. Start the real gateway

Stop the localhost smoke-test gateway, then start the gateway on the chosen address:

```bash
r1-hermes hermes \
  --host <REACHABLE_HOST> \
  --port 18789 \
  --toolsets safe \
  --global-concurrency 2 \
  --per-device-concurrency 1 \
  --ready-file /tmp/r1-hermes.ready
```

Keep the default process caps for normal setup: `--global-concurrency 2` and
`--per-device-concurrency 1`. Raise the global cap only when the human has reviewed the host's
capacity and expects multiple trusted devices to talk at once. Do not raise the per-device cap for
an R1 device unless the human explicitly accepts that one device can occupy multiple Hermes
subprocess slots.

For a long-running setup, use a process supervisor such as systemd/tmux according to the human's environment. The minimal requirement is that the process remains running while Rabbit R1 pairs and sends messages.

Verify the bound address/port locally:

```bash
python - <<'PY'
from pathlib import Path
p = Path('/tmp/r1-hermes.ready')
print(p.read_text().strip() if p.exists() else 'ready file missing')
PY
```

Probe the exact advertised WebSocket URL:

```bash
r1-hermes probe \
  --url ws://<REACHABLE_HOST>:18789/ \
  --message 'Reply with exactly OK'
```

If the probe cannot connect, do not generate a QR yet. Fix reachability first.

## 8. Generate the Rabbit R1 QR PNG

After the exact advertised URL probes successfully:

```bash
r1-hermes qr \
  --host <REACHABLE_HOST> \
  --port 18789 \
  --protocol ws \
  --output ./r1-hermes-secret.png
```

Expected output:

```text
Wrote secret QR PNG: r1-hermes-secret.png
```

Important:

- The PNG contains the bearer token.
- Do not open it in a shared screen or paste the payload text.
- Deliver it only through the current secure channel if media/file delivery is supported.
- In Slack/Hermes gateway contexts, attach it as `MEDIA:/absolute/path/to/r1-hermes-secret.png` rather than printing QR internals.

## 9. Human scan step

Tell the human:

```text
Gateway is running on ws://<REACHABLE_HOST>:18789/. Scan the attached QR with Rabbit R1. The QR contains a bearer secret; I will delete it after pairing unless you want to keep it.
```

Do not claim real-device success until the human confirms R1 scanned it and a message was answered, or you have direct device telemetry proving it.

## 10. Cleanup after pairing

After successful pairing, delete the QR file unless instructed otherwise:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

Keep the gateway token in the environment only for the running gateway. Rotate it if pairing needs to be repeated or if the QR/token may have been exposed.

## 11. Troubleshooting

### `R1_HERMES_GATEWAY_TOKEN is required`

The token env var is missing in the terminal running `r1-hermes`. Re-export it in that terminal. Do not print the token.

### `hermes` command not found

Hermes Agent is not installed or not on `PATH`. Ask the human before installing. If approved, follow the official Hermes Agent install/setup flow and rerun the smoke test.

### Probe connects but Hermes response fails

Check Hermes directly:

```bash
hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with exactly OK'
```

If this fails, fix Hermes configuration/model credentials first. Do not debug Rabbit R1 until Hermes itself works.

### Rabbit R1 cannot connect, but local probe works

Likely network reachability. Verify:

- The QR host is not `127.0.0.1` unless R1 is on the same host, which it is not.
- Gateway is bound to the advertised address.
- Firewall allows the chosen port from Rabbit R1's network.
- Tailscale/LAN routing is available to the R1 device.

### QR generated but wrong host/port

Delete the QR, regenerate with the correct `--host` and `--port`, and keep using the same token only if it was not exposed. Otherwise rotate the token and restart the gateway.

### Need `wss`

Use `--protocol wss` only when a TLS-terminating reverse proxy is actually configured and the public WebSocket URL is reachable. Do not mark a plain `ws://` server as `wss`.

## 12. Final report template

When done, report only:

```text
Set up r1-hermes and verified local/probe flow.
Gateway: ws://<REACHABLE_HOST>:18789/
QR: <attached file or local absolute path>
Tests: python -m pytest -q passed
Hermes smoke: passed
Probe: passed
Security: token/QR contents were not printed; QR should be deleted after pairing.
```

Never include the token, device token, or QR payload JSON in the final report.
