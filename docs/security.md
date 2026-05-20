# Security model

`r1-hermes` treats Rabbit R1 as an untrusted network client until a full `connect` request succeeds.

## Boundaries

- The WebSocket endpoint is the outer trust boundary.
- The gateway token and issued device tokens are bearer secrets.
- Hermes Agent/tool execution is the protected inner boundary.

## Non-negotiable rules

1. No `chat.send` or Hermes handler call before successful `connect` authentication.
2. Default bind is `127.0.0.1`; LAN/Tailscale exposure must be explicit.
3. Never log or render full gateway tokens or device tokens.
4. No unauthenticated admin UI.
5. Device tokens are stored as SHA-256 hashes under a `0700` state directory and bound to the original `device.id`.
6. Rate limit, length limit, and per-device concurrency limit are enforced before Hermes execution.
7. QR payloads contain secrets and must be shared/retained accordingly.

## Recommended deployment

Prefer one of:

- `127.0.0.1` + Tailscale Serve/Funnel with access controls
- `127.0.0.1` + reverse proxy with mTLS or IP allowlist
- LAN bind only on a trusted isolated network

Do not expose raw `ws://0.0.0.0:18789` to the public Internet.

## Pairing flow

1. Operator generates a high-entropy gateway token.
2. Operator builds a Rabbit R1 QR payload containing host, port, protocol, and token.
3. R1 connects and sends `connect` with the gateway token and `device.id`.
4. Adapter issues a per-device token, stores only its hash, and sends the token to the device.
5. Future connects may use the device token only with the same `device.id`.
