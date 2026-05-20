# Running r1-hermes

This is the minimal end-to-end path for a Rabbit R1 to talk to Hermes Agent.

## Prerequisites

- Hermes Agent is installed and `hermes chat -q 'hello'` works on the host.
- The Rabbit R1 can reach the host/port you advertise in the QR payload.
- You have reviewed `docs/security.md`.

## Local smoke test

```bash
python -m pip install -e '.[dev,qr]'
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
r1-hermes hermes --host 127.0.0.1 --port 18789
```

In another terminal, verify Hermes itself is available:

```bash
hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with OK'
```

## Network pairing

Pick the narrowest reachable address. Tailscale is preferred over broad LAN exposure.

```bash
r1-hermes hermes --host 100.x.y.z --port 18789
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

Scan the QR with Rabbit R1. Delete the PNG after pairing:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

## Tool access

Default toolset is `safe`. Expand deliberately:

```bash
r1-hermes hermes --toolsets safe,web
```

Do not enable high-impact toolsets such as `terminal`, `file`, or smart-home controls until the network boundary, pairing flow, and physical access model are reviewed.
