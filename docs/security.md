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

- `127.0.0.1` + Tailscale Serve with tailnet ACLs
- `127.0.0.1` + reverse proxy with mTLS or IP allowlist
- direct bind to a specific Tailscale IP such as `100.x.y.z` when the R1 reaches the host over a tailnet
- LAN bind only on a trusted isolated network

Do not expose raw `ws://0.0.0.0:18789` to the public Internet.

For persistent deployment, use the systemd user-service template in
[`docs/systemd-user-service.md`](systemd-user-service.md). Keep `R1_HERMES_GATEWAY_TOKEN` in the
env file only, verify the `--ready-file`, and run `r1-hermes probe` before pairing a device.

## Pairing flow

1. Operator generates a high-entropy gateway token.
2. Operator builds a Rabbit R1 QR payload containing host, port, protocol, and token.
3. R1 connects and sends `connect` with the gateway token and `device.id`.
4. Adapter issues a per-device token, stores only its hash, and sends the token to the device.
5. Future connects may use the device token only with the same `device.id`.

## QR lifecycle

QR PNGs are bearer-secret material because the payload contains the gateway token. Generate them on
the gateway host or another trusted operator machine only.

- `r1-hermes qr` creates the PNG with owner-only file permissions.
- Existing QR output paths are not overwritten unless `--overwrite` is set.
- The command prints only the output path by default; it prints the payload JSON only with
  `--print-payload`.
- Do not paste QR payload JSON, gateway tokens, device tokens, or raw auth headers into issues,
  pull requests, chat, or logs.
- Delete the QR PNG immediately after successful pairing:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

If pairing must be repeated, create a new gateway token, restart the gateway with that token, and
generate a new QR. Do not reuse a QR after it has left the operator's control.

## Device revoke

Use revoke when an R1 is lost, sold, reset, shared with the wrong operator, or when its device token
may have been captured. Revocation removes the device token hash from the local state file; the
device must complete a fresh gateway-token pairing flow before it can send chat requests again.

```bash
r1-hermes revoke --device-id r1-device-id
```

Use the same `--state-dir` value as the running gateway when it is not the default
`~/.r1-hermes` directory:

```bash
r1-hermes revoke --state-dir /path/to/state --device-id r1-device-id
```

## Incident response

Treat any leaked QR PNG, printed QR payload, gateway token, device token, or raw auth header as a
bearer-secret incident.

1. Stop or firewall the gateway so the exposed secret cannot be used while you rotate it.
2. Delete exposed QR PNGs from the host, terminals, shared folders, and backups where practical.
3. Generate a new `R1_HERMES_GATEWAY_TOKEN` and restart `r1-hermes` with that token.
4. Revoke affected paired devices with `r1-hermes revoke --device-id ...`.
5. Generate a new QR PNG, scan it only on the intended Rabbit R1, then delete the PNG.
6. Run `r1-hermes probe` against the intended private URL to confirm the gateway still requires
   authentication and does not print device tokens.
7. Review local logs, shell history, issue comments, and PR text for accidental secret disclosure;
   redact or rotate again if any bearer secret was copied there.

Do not widen the bind address, publish raw `ws://` service ports to the public Internet, or enable
high-impact Hermes toolsets as part of incident recovery. Restore service only after the network
boundary and fresh pairing flow are understood.
