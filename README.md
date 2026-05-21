# r1-hermes

Secure Rabbit R1 gateway adapter for Hermes Agent.

This repository is intentionally security-first. It implements the Rabbit R1/OpenClaw-compatible WebSocket handshake while avoiding the unsafe properties found in early proof-of-concept shims:

- no agent execution before successful authentication
- localhost bind by default
- no full-token logging
- no unauthenticated admin page
- device tokens are bound to device IDs and stored only as keyed HMAC digests
- device tokens expire by age and idle time, and stale records can be pruned locally
- unauthenticated handshake limits plus authenticated socket, rate, length, global concurrency, and per-device concurrency limits
- explicit install docs and security checklist

Status: runnable MVP. The default runtime is a standalone bridge that accepts Rabbit R1/OpenClaw
WebSocket frames and invokes `hermes chat` for authenticated messages. A native Hermes Gateway
adapter path is prototyped in-library but is not wired into a released Hermes Gateway plugin yet.
Do not expose either mode directly to the public Internet.

![](./assets/IMG_4766.JPG)

![](./assets/IMG_4769.JPG)

## Agent handoff

If you hand this repository to Hermes Agent or another autonomous agent and want it to set up the gateway and produce a Rabbit R1 pairing QR, point it at [`AGENTS.md`](AGENTS.md) and [`docs/agent-setup.md`](docs/agent-setup.md). Those files contain the secret-safe setup runbook, probe commands, QR generation steps, and final report template.

## Install

Supported Python versions: Python 3.10 and newer. CI currently exercises Python 3.10,
3.11, and 3.12.

For normal operator installs, prefer GitHub release artifacts over editable source installs. Download
the wheel and `SHA256SUMS` from the release page, verify the checksum, then install the wheel with
the QR extra:

```bash
sha256sum -c SHA256SUMS
python -m pip install ./r1_hermes-<version>-py3-none-any.whl[qr]
```

The release workflow also publishes dependency reports and GitHub artifact attestations. See
[`docs/release.md`](docs/release.md) for the versioning policy, provenance verification, artifact
contents, and secret-exclusion rules.

Editable installs are for development or autonomous-agent setup from a trusted checkout:

```bash
git clone https://github.com/kandotrun/r1-hermes.git
cd r1-hermes
python -m pip install -e '.[qr]'
```

For development:

```bash
python -m pip install -e '.[dev,qr]'
```

Before opening security-sensitive CI or release changes, also run the local audit and distribution
build checks in [`docs/development-checks.md`](docs/development-checks.md). Dependency advisories
should be fixed by upgrading or tightening vulnerable ranges whenever possible; avoid suppressions
unless the advisory is clearly unreachable and documented.

## 1. Create a gateway token

The token is a bearer secret. Do not paste it into public chats or logs. Runtime, QR, payload,
probe, and `doctor` flows require a URL-safe token with at least 43 characters that does not look
like a placeholder, dummy value, repeated string, or other low-entropy pattern. Generate it with
32 random bytes:

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
  --allowed-device-id r1-known-device-id \
  --toolsets safe,web \
  --authenticated-connection-limit 8 \
  --authenticated-per-device-connection-limit 2 \
  --authenticated-idle-timeout-seconds 300 \
  --global-concurrency 2 \
  --per-device-concurrency 1 \
  --timeout 180 \
  --heartbeat-interval 15 \
  --outbound-text-max-chars 8192 \
  --outbound-event-max-bytes 65536
```

`--timeout` is the deterministic R1 gateway run limit for one authenticated `chat.send`; it also
sets the Hermes subprocess timeout for the default CLI runner. The same value can be supplied with
`R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS`, or the older `R1_HERMES_TIMEOUT` service setting. When the
limit is exceeded, the device receives `CHAT_RUN_TIMEOUT` with a generic gateway-limit message and
the Hermes process is cancelled. While a run is active, the gateway emits generic `running`
heartbeat chat events every `--heartbeat-interval` seconds, configurable with
`R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS`; these events do not include prompts, tool stderr, or
token material.

Assistant text returned to Rabbit R1 is also bounded. `--outbound-text-max-chars` /
`R1_HERMES_OUTBOUND_TEXT_MAX_CHARS` defaults to `8192` characters, and
`--outbound-event-max-bytes` / `R1_HERMES_OUTBOUND_EVENT_MAX_BYTES` defaults to `65536` serialized
JSON bytes per outbound WebSocket event. Oversized Hermes stdout or native gateway proactive sends
are replaced with a deterministic truncated notice, and if the serialized event would still exceed
the byte cap the R1 receives `CHAT_OUTPUT_TOO_LARGE`. Audit logs include only original/sent lengths,
byte counts, limits, and hashes, never the response body. Raise these limits only after checking the
R1 UX and memory impact for large Vision/tool responses.

Toolsets that can affect the host, local files, browsers/desktops, smart-home systems, or vehicles
fail closed for Rabbit R1 sessions. Requests such as `--toolsets terminal,file` or the same value in
`R1_HERMES_TOOLSETS` are rejected unless the operator has explicitly reviewed the network boundary,
pairing flow, physical access model, and command/data access risk, then opts in with
`--allow-high-impact-toolsets` or `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1`. Keep `safe`, or
`safe,web` when web access is intentionally needed, for normal R1 use.

Run diagnostics before pairing or restarting a service. By default, `doctor` reports whether the
effective R1 toolsets match the configured Slack-equivalent bundle without printing tokens or probe
payloads:

```bash
r1-hermes doctor \
  --host 127.0.0.1 \
  --port 18789 \
  --toolsets safe
```

For an intentionally reviewed Slack-equivalent R1 session, set the same effective bundle and the
separate high-impact approval, then make parity a hard preflight check:

```bash
export R1_HERMES_TOOLSETS=safe,web,terminal,file
export R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1
r1-hermes doctor --require-slack-equivalent-toolsets --skip-hermes-smoke
r1-hermes hermes --toolsets "$R1_HERMES_TOOLSETS" --allow-high-impact-toolsets
```

The default Slack-equivalent bundle is `safe,web,terminal,file`. Override it for a deployment with
`R1_HERMES_SLACK_EQUIVALENT_TOOLSETS` or `--slack-equivalent-toolsets` so drift from the local Slack
configuration is visible before a QR scan or service restart.

Use Tailscale Serve, a Tailscale IP, firewall allowlist, or reverse proxy with mTLS or IP
allowlisting when the Rabbit R1 must reach the gateway from another device. Do not use wildcard
binds or expose the raw gateway directly to the public Internet. Copy-paste deployment recipes for
Tailscale Serve and reverse proxies are in [`docs/running.md`](docs/running.md).
If proxyless public reachability is unavoidable, use native TLS with `wss://`, a trusted
certificate, firewall review, and a device allowlist; follow
[`docs/public-wss-native-tls.md`](docs/public-wss-native-tls.md) before generating a QR.

If you know the expected Rabbit R1 `device.id`, lock the gateway to it with repeatable
`--allowed-device-id` options or `R1_HERMES_ALLOWED_DEVICE_IDS`. With an allowlist configured,
unlisted device IDs cannot complete gateway-token pairing and cannot reuse device tokens already
present in `devices.json`; no new token is issued for a rejected ID. For first pairing when the ID
is not yet known, run only on a private boundary, record the exact intended device ID locally from
the state file after successful pairing, use hashed audit logs only for correlation, then restart
with the allowlist.

`R1_HERMES_GLOBAL_CONCURRENCY` defaults to `2`, and `R1_HERMES_PER_DEVICE_CONCURRENCY` defaults
to `1`. A single-user Rabbit R1 deployment should usually keep those defaults. For a reviewed
multi-device deployment, raise the global cap only to the number of simultaneous Hermes subprocesses
the host can absorb, and keep the per-device cap low so one device cannot monopolize the gateway.
Requests over either cap receive a generic `BUSY` response before Hermes is invoked.

Authenticated WebSocket sockets are also bounded before they can sit idle indefinitely.
`R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT` defaults to `8` total authenticated sockets,
`R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT` defaults to `2` per `device.id`,
`R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS` defaults to `300`, and
`R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS` defaults to `3600`. Extra authenticated sockets
receive `CONNECTION_LIMIT` and close with WebSocket policy-violation code `1008`. Idle sockets
receive `AUTHENTICATED_IDLE_TIMEOUT`; sockets over the lifetime cap receive
`AUTHENTICATED_CONNECTION_EXPIRED`. Active chat runs are allowed to finish before idle/lifetime
close policy is applied.

Authenticated `chat.send` requests with an `idempotencyKey` are deduplicated per device and
`sessionKey` in a bounded in-memory cache. A retry while the original Hermes run is still active
receives `BUSY_DUPLICATE` and does not start another subprocess; a retry shortly after completion
receives a duplicate acknowledgement and replay of the cached final or error chat event. If the R1
disconnects after `chat.run_started`, the gateway lets an idempotent run finish so a reconnect can
either see `BUSY_DUPLICATE` while work is active or receive the cached final/error event after work
finishes. If the socket drops while the final event is being sent, the final/error event is cached
before returning so the next retry can recover it. Parser failures such as unsupported media are not
cached and remain safe deterministic errors; repeating a media-bearing send does not invoke Hermes
or echo media bytes. The cache defaults to 256 entries and 5 minutes. Tune with
`R1_HERMES_IDEMPOTENCY_CACHE_MAX_ENTRIES` and `R1_HERMES_IDEMPOTENCY_CACHE_TTL_SECONDS`, or the
matching server CLI options, only when a deployment has reviewed the retry window and memory
tradeoff.

Wildcard bind hosts such as `0.0.0.0`, `::`, and numeric aliases for all interfaces fail closed by default.
If you have explicitly reviewed the network boundary and still need a wildcard bind, opt in with
`--allow-public-bind` or `R1_HERMES_ALLOW_PUBLIC_BIND=1`; otherwise prefer `127.0.0.1` plus
Tailscale Serve, a reverse proxy with mTLS, or a specific Tailscale/LAN IP.

Device tokens expire after 90 days or 30 idle days by default. Tune only when your physical-device
rotation policy requires it:

```bash
r1-hermes hermes \
  --device-token-max-age-seconds 7776000 \
  --device-token-idle-timeout-seconds 2592000
```

The same values can be set with `R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS` and
`R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS`. Use `0` only to disable a specific expiry check.
Expired device tokens cannot reconnect; the Rabbit R1 must scan a fresh QR generated from the
current gateway token.

## 3. Generate the Rabbit R1 QR PNG

For a direct private WebSocket path, such as a concrete Tailscale IP or trusted isolated LAN IP,
write the QR PNG without printing the secret payload JSON:

```bash
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

For Tailscale Serve or a TLS-terminating reverse proxy on port 443, generate the QR for the public
TLS hostname instead of the loopback backend:

```bash
r1-hermes qr \
  --host r1-hermes-host.tailnet-name.ts.net \
  --port 443 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Use `ws://` in the QR only when Rabbit R1 connects directly to the raw `r1-hermes` listener over a
reviewed private, non-TLS path. Use `wss://` when Tailscale Serve, Caddy, nginx, or another reverse
proxy terminates TLS and forwards to `127.0.0.1:18789`. Do not mark a plain `ws://` backend as
`wss://`; the QR protocol must match the URL Rabbit R1 actually dials, not the proxy's upstream URL.
Do not advertise `127.0.0.1` in a real-device QR unless the client is running on the same host.

The QR contains the bearer token. Treat the PNG as a secret and delete it after pairing. The
command creates the PNG as an owner-readable file, refuses to overwrite an existing output path
unless `--overwrite` is set, and does not print the payload JSON unless `--print-payload` is set.

Paired-device state lives under `--state-dir` (default `~/.r1-hermes`). The adapter stores
device-token verifiers as `hmac-sha256:v1` digests keyed by a locally generated
`device-token-hmac.key` file with owner-only permissions; the key is separate from the QR/gateway
token and must not be copied into issues, logs, or PR text. Older unkeyed SHA-256 records are
accepted once for backward compatibility and rewritten as keyed digests after a successful device
token authentication.

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

To revoke every paired device in the state file, use the idempotent all-devices workflow:

```bash
r1-hermes revoke --all
```

Use `--dry-run` first to list affected device IDs without printing token values or changing state.
For gateway-token incidents, use `r1-hermes rotate` to write a fresh token to a local env file and
clear paired devices in one step, then restart the gateway and generate a new QR PNG. See
[`docs/security.md`](docs/security.md) for the full incident response checklist.

To remove expired device records from `devices.json` without affecting still-valid pairings:

```bash
r1-hermes cleanup
```

Pass the same `--state-dir` and expiry settings used by the running gateway if they are not the
defaults. The cleanup command reports only a count; it does not print stored hashes or tokens.

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
- Authenticated WebSocket clients are capped globally and per device, and idle or over-lifetime
  sockets are closed with generic policy errors before they can accumulate indefinitely.
- Each device/session key resumes a stable Hermes CLI session via `hermes chat --continue r1-hermes-...`.
- The gateway enforces global and per-device in-flight caps before starting `hermes chat`.
- High-impact Hermes toolsets such as `terminal`, `file`, smart-home, browser/desktop automation,
  and vehicle controls require an explicit high-impact opt-in before Hermes is configured.
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
R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT=8
R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT=2
R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS=300
R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS=3600
R1_HERMES_RATE_LIMIT_MESSAGES=12
R1_HERMES_RATE_LIMIT_WINDOW_SECONDS=60
R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS=7776000
R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS=2592000
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

For persistent operation from a wheel or sdist install, run `r1-hermes install-systemd-user` to
write the hardened user-service template and env example from the installed package. In a trusted
source checkout, the same templates are also available under `packaging/systemd/` for inspection.
The unit does not contain token literals and uses `--ready-file` for startup readiness. The HTTP
`/healthz` endpoint is local-only by default and returns only `{"ok": true}`; paired-device counts
are diagnostic data and require an explicit `--health-diagnostics` or
`R1_HERMES_HEALTH_DIAGNOSTICS=1` opt-in.

See [`docs/systemd-user-service.md`](docs/systemd-user-service.md) for install, enable, status,
logs, rollback, localhost, and Tailscale examples. For a public native `wss://` listener without a
reverse proxy, use the stricter deployment runbook in
[`docs/public-wss-native-tls.md`](docs/public-wss-native-tls.md).

## Security

Read [`docs/security.md`](docs/security.md) before exposing the gateway to a network.

If you are turning a real Rabbit R1/OpenClaw capture into a public compatibility fixture, use
[`docs/capture-replay.md`](docs/capture-replay.md). Never paste raw captures, QR payloads, auth
headers, gateway tokens, device tokens, device IDs, or personal prompts into public issues or pull
requests.

Report suspected vulnerabilities privately. Use GitHub's private vulnerability reporting or
a draft GitHub Security Advisory when available; otherwise open a minimal issue asking for a
private maintainer contact without including exploit details, gateway tokens, device tokens,
QR payloads, API keys, or raw authorization headers. Public issues are fine for hardening
requests that do not disclose an active exploit or secret.
