# Security model

`r1-hermes` treats Rabbit R1 as an untrusted network client until a full `connect` request succeeds.

For supported Python versions and disclosure routing, see [`../README.md`](../README.md) and
[`../SECURITY.md`](../SECURITY.md).

## Boundaries

- The WebSocket endpoint is the outer trust boundary.
- The gateway token and issued device tokens are bearer secrets.
- Hermes Agent/tool execution is the protected inner boundary.
- The default `r1-hermes hermes` runtime is a subprocess bridge; the prototype native adapter keeps
  the same WebSocket/auth boundary before handing text to any Gateway-style message handler.

## Non-negotiable rules

1. No `chat.send` or Hermes handler call before successful `connect` authentication.
2. Default bind is `127.0.0.1`; LAN/Tailscale exposure must be explicit, and wildcard binds
   require a separate public-bind acknowledgement.
3. Never log or render full gateway tokens or device tokens.
4. No unauthenticated admin UI.
5. Device tokens are stored as keyed HMAC-SHA-256 digests under a `0700` state directory, bound to the original `device.id`, and expire by configured age and idle windows.
6. Unauthenticated handshake limits are enforced by peer IP before authentication.
7. Authenticated rate limit, length limit, global concurrency limit, and per-device concurrency limit are enforced before Hermes execution.
8. QR payloads contain secrets and must be shared/retained accordingly.
9. HTTP health checks expose only minimal readiness by default and stay local-only unless remote
   health access is explicitly reviewed.
10. High-impact Hermes toolsets fail closed for Rabbit R1 sessions unless the operator explicitly
    approves them with the high-impact override after reviewing the deployment boundary.

## Recommended deployment

Prefer one of:

- `127.0.0.1` + Tailscale Serve with tailnet ACLs
- `127.0.0.1` + reverse proxy with mTLS or IP allowlist
- direct bind to a specific Tailscale IP such as `100.x.y.z` when the R1 reaches the host over a tailnet
- LAN bind only on a trusted isolated network

Wildcard bind hosts such as `0.0.0.0`, `::`, and numeric aliases for all interfaces fail closed
unless `--allow-public-bind` or `R1_HERMES_ALLOW_PUBLIC_BIND=1` is set. Do not use that opt-in for
convenience. Use it only after reviewing the network boundary, firewall, and client path, and do
not expose a raw `ws://0.0.0.0:18789` service to the public Internet.

For persistent deployment, use the systemd user-service template in
[`docs/systemd-user-service.md`](systemd-user-service.md). Keep `R1_HERMES_GATEWAY_TOKEN` in the
env file only, verify the `--ready-file`, and run `r1-hermes probe` before pairing a device.

`/healthz` returns only `{"ok": true}` by default and rejects non-local peers. If an external
supervisor genuinely needs the endpoint, set `--allow-remote-health` or
`R1_HERMES_ALLOW_REMOTE_HEALTH=1` only after reviewing the network boundary. Paired-device counts
are omitted unless `--health-diagnostics` or `R1_HERMES_HEALTH_DIAGNOSTICS=1` is also set; do not
enable diagnostic counts for public or broadly reachable health probes.

## Hermes execution boundary

In standalone mode, `r1-hermes` starts Hermes with `asyncio.create_subprocess_exec` and an argument
vector, not through a shell. R1 message text is passed as the `--query` argument only after
successful `connect`, length/rate/concurrency checks, and payload normalization. Gateway tokens,
device tokens, QR payloads, and raw auth headers are never needed by the Hermes subprocess and must
not be added to command-line arguments, environment logging, or error responses.

The default process caps are intentionally small: `R1_HERMES_GLOBAL_CONCURRENCY=2` across the whole
gateway and `R1_HERMES_PER_DEVICE_CONCURRENCY=1` per authenticated device ID. Keep these defaults
for a single Rabbit R1 or a small personal deployment. For multiple trusted devices, raise the
global cap only after sizing CPU, memory, model/API limits, and expected Hermes runtime duration;
avoid raising the per-device cap unless the operator explicitly accepts that one physical device can
consume multiple Hermes subprocess slots. When a cap is reached, the gateway returns a generic
`BUSY` response and does not start Hermes.

Hermes tool access defaults to `safe`. Lower-risk expansion such as `safe,web` is allowed when the
operator intentionally wants web access. High-impact toolsets are treated as host or environment
control surfaces and fail closed when requested from `--toolsets` or `R1_HERMES_TOOLSETS`; this
includes `terminal`, `shell`, `file`/`filesystem`, browser or desktop automation, smart-home/home
automation, and vehicle/automotive controls. Approve them only after reviewing the network
boundary, QR and device-token handling, physical access model, and the data or command execution
available through that toolset:

```bash
r1-hermes hermes \
  --toolsets terminal,file \
  --allow-high-impact-toolsets
```

The same opt-in can be supplied as `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1` for supervised service
configuration. Do not use the override to work around public exposure, weak pairing controls, or an
unclear physical-device ownership model.

The native Gateway prototype in `src/r1_hermes/native_gateway.py` preserves the same preconditions:
it converts only authenticated `chat.send` text into a gateway-style message event, excludes message
text from event `repr()`, stores no bearer token in metadata, sanitizes platform toolset metadata so
high-impact toolsets cannot silently elevate without the explicit high-impact allowlist, and makes
`send_text()` a no-op when there is no active authenticated WebSocket for the target device/session.
A future Hermes-core adapter or plugin must retain these properties before it can replace the
standalone bridge.

## Pairing flow

1. Operator generates a high-entropy gateway token.
2. Operator builds a Rabbit R1 QR payload containing host, port, protocol, and token.
3. R1 connects and sends `connect` or the compatible `gateway.connect` variant with the gateway
   token and `device.id`.
4. Adapter issues a per-device token, stores only a keyed digest, and sends the token to the device.
5. Future connects may use the device token only with the same `device.id`.

## Device-token storage

The state directory contains two local files:

- `devices.json`: paired device IDs, display names, timestamps, and device-token verifier digests.
- `device-token-hmac.key`: a locally generated HMAC key used only for device-token digests.

Both files are written with owner-only `0600` permissions, and the state directory is forced to
`0700`. New records use `hmac-sha256:v1:<digest>` rather than raw `SHA-256(token)`, so copying only
`devices.json` is not enough to perform offline token analysis without the local HMAC key. The HMAC
key is not derived from `R1_HERMES_GATEWAY_TOKEN`, is not included in QR payloads, and must not be
logged, pasted into issue comments, committed, or sent to Rabbit R1.

Existing local records from older releases may contain unkeyed SHA-256 device-token hashes. For
backward compatibility, a valid legacy device token is accepted once and the record is rewritten as
an `hmac-sha256:v1` digest immediately after successful authentication. Invalid legacy tokens are
not upgraded. If the local HMAC key file is deleted while `devices.json` remains, already keyed
records cannot be verified with the newly generated key; revoke or remove the stale state and pair
the device again with a fresh QR.

## Device token lifetime

Device tokens are intentionally not permanent. By default, a paired device token expires after 90
days from issuance or after 30 days without a successful device-token reconnect, whichever happens
first:

```text
R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS=7776000
R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS=2592000
```

Both settings can also be passed to `r1-hermes serve` or `r1-hermes hermes` as
`--device-token-max-age-seconds` and `--device-token-idle-timeout-seconds`. A value of `0` disables
that specific check; do this only for a documented local test or a reviewed deployment exception.

When a token expires, the gateway rejects it with the same generic unauthorized response used for
other token failures and does not call Hermes. The Rabbit R1 operator should generate a fresh
gateway token, restart the gateway with it, create a new QR PNG, scan it on the device, then delete
the QR PNG after pairing.

Existing `devices.json` records created before these timestamp fields existed are accepted
backward-compatibly. On first load, missing `created_at_ms` or `last_seen_at_ms` values are treated
as the current local load time so old pairings are not invalidated immediately by a software update.
After the first successful reconnect or cleanup rewrite, records are stored with explicit
timestamps.

Prune expired records from the local state file with:

```bash
r1-hermes cleanup
```

Use the same `--state-dir`, `--device-token-max-age-seconds`, and
`--device-token-idle-timeout-seconds` values as the running gateway when they are not defaults:

```bash
r1-hermes cleanup \
  --state-dir /path/to/state \
  --device-token-max-age-seconds 7776000 \
  --device-token-idle-timeout-seconds 2592000
```

Cleanup removes only expired device records and prints a count. It does not print token hashes,
device tokens, gateway tokens, QR payloads, or raw authorization material.

## OpenClaw/Rabbit compatibility scope

The authenticated handshake accepts the standard `connect` method and the OpenClaw/Rabbit
`gateway.connect` method. Both methods use the same parser, gateway-token/device-token checks,
device-token issuance, device ID binding, timeout, and unauthenticated request boundary.

After successful `gateway.connect`, the gateway still returns the existing `hello-ok` response and
also emits compatibility acknowledgement events named `connect.ok` and `node.pair.approved`. Those
events intentionally contain only acknowledgement metadata such as `ok`, `deviceId`, and timestamp;
they do not include the gateway token or issued device token. Failed or malformed handshakes do not
emit acknowledgement events and do not invoke Hermes.

Unauthenticated clients are tracked by remote peer IP, with a conservative fallback key when peer
metadata is unavailable. The gateway sends only generic `RATE_LIMITED`, `UNAUTHORIZED`, or malformed
request errors, closes abusive unauthenticated sockets with WebSocket policy-violation code `1008`,
and does not echo supplied auth tokens, device IDs, QR payloads, or raw authorization headers.

## Handshake rate-limit settings

The default limits are in-memory and reset when the process restarts:

```text
R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS=60
R1_HERMES_UNAUTHENTICATED_COOLDOWN_SECONDS=60
R1_HERMES_UNAUTHENTICATED_TIMEOUT_SECONDS=30
```

`R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT` limits concurrent pre-authentication sockets per peer.
`R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT` counts malformed frames, unauthenticated requests, and
failed `connect` attempts before a short cooldown begins. These controls are defense in depth; they
do not make direct public-Internet exposure acceptable.

This project does not implement unauthenticated pairing, browser admin pairing, arbitrary method
forwarding, binary capture replay, or non-chat Rabbit services. Unsupported methods continue to
receive a generic `UNKNOWN_METHOD` response after authentication.

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
may have been captured. Revocation removes the device-token digest from the local state file; the
device must complete a fresh gateway-token pairing flow before it can send chat requests again.

```bash
r1-hermes revoke --device-id r1-device-id
```

Use the same `--state-dir` value as the running gateway when it is not the default
`~/.r1-hermes` directory:

```bash
r1-hermes revoke --state-dir /path/to/state --device-id r1-device-id
```

Expiration is not a replacement for revocation. If compromise is suspected, revoke immediately and
rotate the gateway token instead of waiting for the age or idle timer.

For a broad incident or an unknown affected set, revoke all paired devices in one command. The
command is idempotent when the state file is empty and never prints device token values:

```bash
r1-hermes revoke --state-dir /path/to/state --all
```

Preview the affected device IDs without changing the state file:

```bash
r1-hermes revoke --state-dir /path/to/state --all --dry-run
```

## Gateway token rotation

Use `rotate` when the gateway token, QR payload, QR PNG, shell history, or raw auth header may have
leaked. The command generates a fresh `R1_HERMES_GATEWAY_TOKEN`, optionally writes it to a local
env file, and revokes all paired devices so old device tokens cannot continue authenticating:

```bash
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env
```

Default output confirms the env-file path and affected device IDs only; it does not print the new
gateway token. Use `--dry-run` to inspect the planned env-file update and device revocation first:

```bash
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env \
  --dry-run
```

Only use `--print-token` when you intentionally need to copy the fresh bearer secret into another
secret store. Its output is explicitly labeled as secret and must not be pasted into issues, PRs,
chat, logs, screenshots, or shared terminals.

## Incident response

Treat any leaked QR PNG, printed QR payload, gateway token, device token, or raw auth header as a
bearer-secret incident.

1. Stop or firewall the gateway so the exposed secret cannot be used while you rotate it.
2. Delete exposed QR PNGs from the host, terminals, shared folders, and backups where practical.
3. Run `r1-hermes rotate --state-dir <state-dir> --env-file <env-file>` to write a new gateway
   token and revoke all paired device tokens without printing secrets.
4. Restart `r1-hermes` so the new token is loaded.
5. Generate a new QR PNG, scan it only on the intended Rabbit R1, then delete the PNG.
6. Run `r1-hermes probe` against the intended private URL to confirm the gateway still requires
   authentication and does not print device tokens.
7. Review local logs, shell history, issue comments, and PR text for accidental secret disclosure;
   redact or rotate again if any bearer secret was copied there.

Do not widen the bind address, publish raw `ws://` service ports to the public Internet, or enable
high-impact Hermes toolsets as part of incident recovery. Restore service only after the network
boundary and fresh pairing flow are understood.

## Responsible disclosure

Report suspected vulnerabilities privately. Prefer GitHub private vulnerability reporting or a
draft GitHub Security Advisory when available. If neither is available to you, open a minimal
GitHub issue requesting a private maintainer contact and do not include exploit steps, gateway
tokens, device tokens, QR payload contents, API keys, raw authorization headers, logs containing
secrets, or real Rabbit R1 captures.

Public issues are appropriate for general hardening ideas that do not disclose an active exploit,
secret, or bypass. Authentication bypasses, token disclosure, unsafe public exposure, shell
injection, and command execution boundary failures are treated as release blockers.
