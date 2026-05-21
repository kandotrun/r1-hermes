# Running r1-hermes

This is the minimal end-to-end path for a Rabbit R1 to talk to Hermes Agent.

If an autonomous agent is doing the setup and QR generation for you, use [`docs/agent-setup.md`](agent-setup.md) plus the repository-level [`AGENTS.md`](../AGENTS.md). Those files are written as an agent runbook and include secret-handling requirements.

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
r1-hermes hermes --host 127.0.0.1 --port 18789 --ready-file /tmp/r1-hermes.ready
```

In another terminal, verify Hermes itself is available:

```bash
hermes chat --quiet --source r1-hermes-smoke --toolsets safe --query 'Reply with OK'
```

You can also run the built-in diagnostics before pairing. `doctor` checks token presence, state
directory and secret-file permissions, bind host/port safety, effective R1 toolset parity with the
configured Slack-equivalent bundle, Hermes CLI availability, optional gateway probing, and an
optional QR output path without printing bearer values, QR payload JSON, device tokens, raw auth
headers, or the smoke-test prompt.

```bash
r1-hermes doctor \
  --state-dir ~/.r1-hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --qr-output ./r1-hermes-secret.png
```

Exit code policy: `FAIL` checks return non-zero; `WARN` checks return zero but should be reviewed.
Expected first-run warnings include a missing state directory that the gateway will create as
`0700`, a safe/minimal toolset list that does not match Slack-equivalent access, a skipped gateway
probe when `--url` is omitted, and a localhost reachability reminder.

## Network pairing

Pick the narrowest reachable address. Tailscale is preferred over broad LAN exposure.

```bash
r1-hermes hermes --host 100.x.y.z --port 18789
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

If the expected Rabbit R1 `device.id` is already known, start the gateway in locked-down mode:

```bash
r1-hermes hermes \
  --host 100.x.y.z \
  --port 18789 \
  --allowed-device-id r1-known-device-id
```

`--allowed-device-id` is repeatable. `R1_HERMES_ALLOWED_DEVICE_IDS` accepts comma, space, or
newline-separated IDs for service env files. When configured, unlisted IDs are rejected before a
gateway-token pairing can issue a device token, and existing `devices.json` records for unlisted
IDs cannot reconnect with their old device tokens.

For first pairing when you do not know the R1 ID yet, leave the allowlist unset only on a private
network boundary such as localhost through Tailscale Serve, a specific Tailscale IP, or an mTLS/IP
allowlisted proxy. Pair once, record the intended device ID from the local state file or the
sanitized device ID hash in local audit logs for correlation, then restart the gateway with
`--allowed-device-id` or `R1_HERMES_ALLOWED_DEVICE_IDS` before normal use. Do not paste raw device
IDs from real captures or logs into issues or pull requests.

The gateway starts at most two Hermes subprocess-backed chat runs at once by default, and at most
one per authenticated device ID:

```bash
r1-hermes hermes \
  --host 100.x.y.z \
  --port 18789 \
  --authenticated-connection-limit 8 \
  --authenticated-per-device-connection-limit 2 \
  --authenticated-idle-timeout-seconds 300 \
  --authenticated-max-lifetime-seconds 3600 \
  --global-concurrency 2 \
  --per-device-concurrency 1
```

The same chat-run settings can be supplied through `R1_HERMES_GLOBAL_CONCURRENCY` and
`R1_HERMES_PER_DEVICE_CONCURRENCY`. Keep `2`/`1` for one personal Rabbit R1. For multiple trusted
devices, increase only the global cap to the number of concurrent Hermes subprocesses the host can
comfortably run; keep the per-device cap low unless one device is intentionally allowed to occupy
several slots. Requests over either chat-run cap receive `BUSY` before Hermes is invoked.

Authenticated WebSocket sockets have separate caps and lifetime policy. Defaults allow eight total
authenticated sockets, two per Rabbit R1 `device.id`, five idle minutes, and one hour maximum
lifetime. Tune these with `R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT`,
`R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT`,
`R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS`, and
`R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS`. Extra authenticated sockets receive
`CONNECTION_LIMIT` and close with WebSocket policy-violation code `1008`; idle sockets receive
`AUTHENTICATED_IDLE_TIMEOUT`; over-lifetime sockets receive `AUTHENTICATED_CONNECTION_EXPIRED`.
The idle and lifetime monitor waits while a chat run is active, so a normal probe or long Hermes run
is not interrupted just because no additional frames arrive while Hermes is working.

For one personal R1, keep the defaults or lower the per-device socket cap to `1` if your client
does not keep a reconnect socket open. For a reviewed multi-device deployment, set the global socket
cap slightly above the expected number of simultaneously paired devices and keep the per-device cap
small so one leaked device token cannot occupy all file descriptors.

Each authenticated R1 chat run has an explicit gateway timeout. The default is 180 seconds. Set it
with `--timeout`, `R1_HERMES_CHAT_RUN_TIMEOUT_SECONDS`, or the legacy service value
`R1_HERMES_TIMEOUT`. The default Hermes CLI runner receives the same timeout, so a slow subprocess
is killed at the gateway limit and the R1 receives `CHAT_RUN_TIMEOUT` with a generic message that
the run exceeded the R1 gateway timeout limit. The gateway sends generic `running` heartbeat chat
events every 15 seconds while Hermes is still active; tune that cadence with `--heartbeat-interval`
or `R1_HERMES_CHAT_HEARTBEAT_INTERVAL_SECONDS`. Heartbeats intentionally expose only run/session
metadata and a fixed status string, never tool stderr, prompts, tokens, or QR payload material.

The gateway rejects wildcard bind hosts such as `0.0.0.0`, `::`, and numeric aliases for all
interfaces unless you explicitly acknowledge the exposure with `--allow-public-bind` or
`R1_HERMES_ALLOW_PUBLIC_BIND=1`. Treat that opt-in as an exception for a reviewed private network
boundary only. Prefer `127.0.0.1` with Tailscale Serve, `127.0.0.1` behind a reverse proxy with
mTLS or IP allowlisting, or a concrete Tailscale/LAN IP.

If Rabbit R1 must connect to a public endpoint without a reverse proxy, do not expose cleartext
`ws://`. Use the native TLS public WSS runbook in
[`public-wss-native-tls.md`](public-wss-native-tls.md) for hostname selection, Let's Encrypt
issuance, certificate permissions, `R1_HERMES_ALLOWED_DEVICE_IDS`, systemd compatibility overrides,
renewal checks, and rollback.

### Tailscale Serve recipe

Use this when the Rabbit R1 can reach a Tailscale HTTPS service name. `r1-hermes` stays bound to
loopback, while Tailscale handles the reachable TLS listener and tailnet access controls.

In the private shell that starts the gateway, source or export `R1_HERMES_GATEWAY_TOKEN` without
echoing it, then start the raw gateway on localhost only:

```bash
r1-hermes hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --ready-file /tmp/r1-hermes.ready
```

Publish that local listener through Tailscale Serve:

```bash
tailscale serve --bg --https=443 127.0.0.1:18789
tailscale serve status
```

If your installed Tailscale CLI requires an explicit HTTP upstream URL, use this equivalent form:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:18789
```

Verify the gateway is not listening on a raw public interface:

```bash
ss -ltnp | grep ':18789'
```

Expected: the `r1-hermes` listener is on `127.0.0.1:18789` or `[::1]:18789`, not `0.0.0.0:18789`
or `[::]:18789`. If it is wider than loopback, stop the gateway and restart it with
`--host 127.0.0.1` before pairing.

Verify the Tailscale HTTPS URL from a tailnet client that should be allowed:

```bash
curl --fail --silent https://r1-hermes-host.tailnet-name.ts.net/healthz
r1-hermes probe \
  --url wss://r1-hermes-host.tailnet-name.ts.net/ \
  --message 'Reply with OK from Hermes'
```

From a machine or network that is not allowed by your tailnet ACLs, the same HTTPS URL must fail to
connect or return an access error. It must not return HTTP 200:

```bash
curl --fail --silent --show-error https://r1-hermes-host.tailnet-name.ts.net/healthz
```

Generate the QR for the Tailscale HTTPS service name, not for `127.0.0.1`:

```bash
r1-hermes qr \
  --host r1-hermes-host.tailnet-name.ts.net \
  --port 443 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Do not use `tailscale funnel` for this service unless a human explicitly approves public-Internet
exposure and compensating controls. Tailscale Serve keeps the endpoint inside the tailnet; Funnel
is a different public exposure model.

### Reverse proxy recipe

Use this when Rabbit R1 must connect through an HTTPS hostname outside localhost. Keep the backend
bound to loopback and put the network policy at the proxy. The example below uses Caddy with either
mTLS or an explicit source IP allowlist; keep at least one of those controls enabled.

Start the backend on loopback:

```bash
r1-hermes hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --ready-file /tmp/r1-hermes.ready
```

Use a Caddyfile like this for mTLS:

```caddyfile
r1.example.com {
    tls {
        client_auth {
            mode require_and_verify
            trust_pool file /etc/caddy/r1-client-ca.pem
        }
    }

    reverse_proxy 127.0.0.1:18789
}
```

If mTLS is not available, use an explicit allowlist at the proxy and keep the list as narrow as the
Rabbit R1's real egress path allows:

```caddyfile
r1.example.com {
    @allowed remote_ip 198.51.100.10 203.0.113.0/24 2001:db8:1234::/48
    @not_allowed not remote_ip 198.51.100.10 203.0.113.0/24 2001:db8:1234::/48

    respond @not_allowed "forbidden" 403
    reverse_proxy @allowed 127.0.0.1:18789
}
```

If the proxy is behind another trusted proxy or load balancer, configure Caddy's trusted-proxy
handling before relying on `remote_ip`; otherwise allowlisting may check the proxy address instead
of the original client.

Verify backend bind and local readiness on the gateway host:

```bash
ss -ltnp | grep ':18789'
curl --fail --silent http://127.0.0.1:18789/healthz
```

Verify the trusted path succeeds from an allowed client:

```bash
curl --fail --silent https://r1.example.com/healthz
r1-hermes probe --url wss://r1.example.com/ --message 'Reply with OK from Hermes'
```

Verify the failure case from an untrusted network before generating a QR. The request must fail TLS
client-certificate authentication, return 403, time out, or otherwise fail closed; it must not
return HTTP 200:

```bash
curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}\n' \
  https://r1.example.com/healthz
```

Generate the QR for the proxy's external TLS URL:

```bash
r1-hermes qr \
  --host r1.example.com \
  --port 443 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Keep Caddy/nginx access logs and shell transcripts free of QR payload JSON, gateway tokens, device
tokens, and raw authorization headers. Do not use `--print-payload` in deployment runbooks.

### QR protocol selection

Use `ws://` in the QR only when Rabbit R1 connects directly to the raw `r1-hermes` listener over a
reviewed private, non-TLS path such as a concrete Tailscale IP or isolated LAN IP. Use `wss://`
when Tailscale Serve, Caddy, nginx, or another reverse proxy terminates TLS and forwards to the
loopback backend. Do not mark a plain `ws://` backend as `wss://`; the QR protocol must match the
URL Rabbit R1 actually dials, not the proxy's upstream URL. Do not advertise `127.0.0.1` in a
real-device QR unless the client is running on the same host.

`r1-hermes qr` writes an owner-only PNG and fails if `--output` already exists. Use `--overwrite`
only after confirming the old PNG is no longer needed. The command intentionally does not print the
secret payload JSON unless `--print-payload` is supplied.

Pairing state is stored in the gateway state directory. New device-token records are keyed
`hmac-sha256:v1` digests in `devices.json`, and the local HMAC key is stored separately as
`device-token-hmac.key` with owner-only permissions. Older unkeyed SHA-256 device-token records are
rewritten to keyed digests after the next successful device-token authentication. If the HMAC key is
lost while keeping keyed `devices.json` records, remove or revoke the stale records and re-pair the
Rabbit R1 with a fresh gateway token and QR.

Before scanning with the real device, probe the exact WebSocket flow from this machine:

```bash
r1-hermes probe --url ws://100.x.y.z:18789/ --message 'Reply with OK from Hermes'
```

For an agent- or operator-friendly pre-scan report, run doctor against the same advertised URL:

```bash
r1-hermes doctor \
  --state-dir ~/.r1-hermes \
  --host 100.x.y.z \
  --port 18789 \
  --url ws://100.x.y.z:18789/ \
  --qr-output ./r1-hermes-secret.png
```

Do not generate or scan the QR while doctor reports missing token, unsafe state permissions,
wildcard bind without an explicit reviewed opt-in, missing Hermes CLI, or failed probe checks.

The HTTP `/healthz` endpoint is for readiness only. By default it is local-only and returns no
paired-device state:

```bash
curl --fail --silent http://127.0.0.1:18789/healthz
```

```json
{"ok": true}
```

Use `--allow-remote-health` only for a reviewed private monitoring path. Use
`--health-diagnostics` only when local diagnostics need the paired-device count.

The gateway rate-limits pre-authentication noise by peer IP. Defaults allow eight unauthenticated
connections and eight malformed or failed handshake attempts per 60-second window, followed by a
60-second cooldown. Tune these only through the environment, for example in a private service env
file:

```ini
R1_HERMES_UNAUTHENTICATED_CONNECTION_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_LIMIT=8
R1_HERMES_UNAUTHENTICATED_ATTEMPT_WINDOW_SECONDS=60
R1_HERMES_UNAUTHENTICATED_COOLDOWN_SECONDS=60
R1_HERMES_UNAUTHENTICATED_TIMEOUT_SECONDS=30
R1_HERMES_AUTHENTICATED_CONNECTION_LIMIT=8
R1_HERMES_AUTHENTICATED_PER_DEVICE_CONNECTION_LIMIT=2
R1_HERMES_AUTHENTICATED_IDLE_TIMEOUT_SECONDS=300
R1_HERMES_AUTHENTICATED_MAX_LIFETIME_SECONDS=3600
```

The unauthenticated limits protect the handshake path. The authenticated limits bound already
paired sockets after `connect` succeeds. Neither set is a substitute for Tailscale, firewall rules,
mTLS, or IP allowlisting.

Device tokens expire after 90 days from pairing or 30 idle days by default:

```ini
R1_HERMES_DEVICE_TOKEN_MAX_AGE_SECONDS=7776000
R1_HERMES_DEVICE_TOKEN_IDLE_TIMEOUT_SECONDS=2592000
```

When either limit is reached, Rabbit R1 must scan a fresh QR generated from the current gateway
token. To prune stale records from `devices.json` while keeping valid devices paired, run:

```bash
r1-hermes cleanup
```

Use the same `--state-dir` and expiry values as the running gateway if you customized them.

For compatibility debugging, `--dump-frames` prints sanitized WebSocket frames to stderr. Auth token
fields and text/audio content fields are redacted before printing; still keep the output local until
you have reviewed it.

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --message 'Reply with OK from Hermes' \
  --dump-frames
```

For real Rabbit R1 media compatibility gaps, prefer server-side frame-shape diagnostics over raw
frame dumps. Start the gateway with `--frame-shape-logging` or
`R1_HERMES_FRAME_SHAPE_LOGGING=1` on the same private network boundary you already reviewed for
pairing:

```bash
R1_HERMES_FRAME_SHAPE_LOGGING=1 r1-hermes hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --toolsets safe
```

The gateway then emits `frame.shape` audit events for request frames. These events include method
names, object key sets, list lengths, string lengths, safe enum-like protocol values, media-field
presence, and media-field paths. They do not include gateway tokens, issued device tokens, raw
device IDs, prompt text, URLs, base64 strings, or media bytes. Unknown media methods still receive
the generic `UNKNOWN_METHOD` response after authentication, and unauthenticated frames still receive
`UNAUTHENTICATED`; the diagnostic flag does not enable new behavior or Hermes execution.

Capture only the local audit line, not the raw frame, when asking maintainers what parser support
is needed:

```bash
journalctl --user-unit r1-hermes.service --since '10 minutes ago' -o cat | grep '"frame.shape"'
```

When the shape shows a compatibility gap, keep any raw WebSocket capture outside the repository and
turn it into a sanitized fixture with [`capture-replay.md`](capture-replay.md). Pass every known
private value with `--forbid`, replace media bytes with `DUMMY_BINARY_DATA_OMITTED`, and commit only
the sanitized JSON plus focused parser/replay tests.

The default probe uses the standard `connect` handshake. To smoke-test the OpenClaw/Rabbit
`gateway.connect` variant and its compatibility acknowledgement events, run:

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --connect-method gateway.connect \
  --message 'Reply with OK from Hermes'
```

### Media chat scope

The standalone bridge accepts authenticated text plus image `chat.send` requests. Plain text fields
and text content parts such as `{"type":"text","text":"..."}` or `{"type":"input_text",...}`
are normalized into the Hermes prompt. Accepted PNG, JPEG, and WebP image parts are base64 decoded,
MIME-sniffed, capped by `R1_HERMES_MEDIA_MAX_FILE_BYTES` (default 10 MiB), and written under
`<state-dir>/uploads/` with owner-only directory and file permissions. Hermes receives each image as
`MEDIA:/absolute/path` followed by the user's text so the existing Vision path can consume it.

Uploaded image files are removed after the Hermes run finishes or is cancelled, and stale files are
pruned by `R1_HERMES_MEDIA_TTL_SECONDS` (default 15 minutes) before new uploads are accepted. Audio,
video, non-image files, remote media URLs, unsupported extensions, oversized media, and malformed
base64 are rejected before Hermes is invoked. Media bytes and full prompts are not written to audit
logs or probe frame dumps. Do not commit raw Rabbit R1 audio/image captures; use sanitized fixtures
with dummy placeholder strings or tiny public test images when adding parser coverage.

## Audit logs

The gateway emits one-line JSON audit events through the `r1_hermes.audit` logger. These logs are
intended for local operator debugging and include stable hashes, counts, error codes, limits, and
durations. They do not include gateway tokens, device tokens, QR payloads, raw authorization
headers, raw device IDs, full run IDs, Hermes stderr, or full user prompts by default.
The CLI configures standard Python logging for `serve` and `hermes`; set `R1_HERMES_LOG_LEVEL` if
you need to raise or lower verbosity.

Useful event names include:

- `connect.challenge_issued` at `INFO` when a WebSocket challenge is sent.
- `auth.success` at `INFO` when `connect` or `gateway.connect` authenticates.
- `auth.failure` and `auth.parser_error` at `WARNING` for bad tokens versus malformed handshake
  payloads.
- `rate_limited` and `busy_rejected` at `WARNING` before Hermes is invoked.
- `auth.connection_rejected` and `auth.connection_closed` for authenticated socket caps and
  idle/lifetime policy closes.
- `chat.run_started`, `chat.run_final`, and `chat.run_error` for authenticated run lifecycle.
- R1-visible heartbeat frames use `event: chat`, `state: running`, and `heartbeat: true`; they are
  not audit logs and contain no prompt or tool output.
- `frame.shape` at `INFO` only when frame-shape diagnostics are explicitly enabled.
- `hermes.subprocess_failed` and `hermes.subprocess_timeout` for Hermes CLI failures.
- `device.revoke`, `device.revoke_all`, and `device.cleanup` for local device-state operations.

With the systemd user service, inspect recent audit events with journald:

```bash
journalctl --user-unit r1-hermes.service --since today -o cat | grep '"event"'
journalctl --user-unit r1-hermes.service -f -o cat | grep '"event"'
```

Representative redacted events look like:

```json
{"auth_type":"gateway_token","device_id_hash":"sha256:0123456789abcdef","device_token_rotated":false,"event":"auth.success","level":"info","method":"connect","ts_ms":1710000000000}
{"device_id_hash":"sha256:0123456789abcdef","error_code":"CHAT_RUN_FAILED","event":"chat.run_error","level":"error","run_id_hash":"sha256:abcdef0123456789","safe_message":"chat run failed","session_key_hash":"sha256:1111222233334444","ts_ms":1710000000000}
```

If an audit line contains a bearer secret or raw authorization material, treat it as an incident:
stop or firewall the gateway, rotate `R1_HERMES_GATEWAY_TOKEN`, revoke affected devices, and review
local log retention before pairing again.

Scan the QR with Rabbit R1. Delete the PNG after pairing:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

To reissue a QR, delete or overwrite the old PNG, generate a new gateway token, restart the gateway
with that token, and run `r1-hermes qr` again. If the device was already paired, revoke its stored
device token before re-pairing:

```bash
r1-hermes revoke --device-id r1-device-id
```

For a QR or gateway-token incident, prefer the one-command rotation workflow so all paired device
tokens are cleared while the env-file token is replaced:

```bash
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env
```

Use `r1-hermes revoke --all --dry-run` or `r1-hermes rotate --dry-run` to preview affected device
IDs without printing token values.

## Adding real-device compatibility captures

Add only sanitized JSON fixtures under `tests/fixtures/r1_payloads/`. Capture the smallest frame set
needed to reproduce a compatibility gap:

- the QR payload shape generated by the helper script, with `token` replaced by
  `DUMMY_GATEWAY_TOKEN_DO_NOT_USE`
- the first authenticated handshake request, including method name and non-secret capability fields
- one representative `chat.send` frame, with private message content replaced by a neutral test
  phrase
- the names of acknowledgement events observed after connect, such as `connect.ok` or
  `node.pair.approved`
- parser-relevant field names and nesting, while removing exact IPs, account identifiers, raw
  timestamps, audio/image payloads, and unrelated UI state; unsupported-media fixtures may keep
  dummy placeholders such as `DUMMY_BINARY_DATA_OMITTED` only

Redaction is mandatory before committing or pasting captures anywhere. Replace gateway tokens,
device tokens, QR secrets, API keys, cookies, bearer headers, and raw auth headers with obvious
dummy values such as `DUMMY_GATEWAY_TOKEN_DO_NOT_USE` or `DUMMY_DEVICE_TOKEN_DO_NOT_USE`. Do not
commit raw packet captures, screenshots containing QR codes, or terminal logs that include
unreviewed frames.

## Tool access

Default toolset is `safe`. Lower-risk expansion such as web access is allowed when it is intentional:

```bash
r1-hermes hermes --toolsets safe,web
```

Run `doctor` as a preflight before pairing or restarting so drift is visible in one command:

```bash
r1-hermes doctor \
  --host 127.0.0.1 \
  --port 18789 \
  --toolsets safe \
  --skip-hermes-smoke
```

That safe/minimal mode intentionally differs from the Slack-equivalent bundle, so doctor reports a
warning while still exiting zero unless `--require-slack-equivalent-toolsets` is set. Use it for
normal Rabbit R1 operation unless the operator has explicitly approved broader access.

For an intentionally reviewed Slack-equivalent mode, make the configured bundle explicit and keep
the high-impact approval separate:

```bash
export R1_HERMES_TOOLSETS=safe,web,terminal,file
export R1_HERMES_SLACK_EQUIVALENT_TOOLSETS=safe,web,terminal,file
export R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1

r1-hermes doctor \
  --require-slack-equivalent-toolsets \
  --skip-hermes-smoke

r1-hermes hermes \
  --toolsets "$R1_HERMES_TOOLSETS" \
  --allow-high-impact-toolsets
```

The built-in Slack-equivalent bundle is `safe,web,terminal,file`. If the actual Slack deployment
uses a different reviewed bundle, set `R1_HERMES_SLACK_EQUIVALENT_TOOLSETS` or pass
`--slack-equivalent-toolsets` to doctor. Without `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1` or
`--allow-high-impact-toolsets`, doctor fails whenever the effective R1 toolsets include high-impact
entries, which catches the same misconfiguration that would prevent `r1-hermes hermes` from
starting.

High-impact toolsets fail closed for Rabbit R1 sessions. Requests such as
`--toolsets terminal,file`, or `R1_HERMES_TOOLSETS=terminal,file`, are rejected unless the operator
also passes `--allow-high-impact-toolsets` or sets `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1`.

Use that override only after reviewing the network boundary, QR and token handling, pairing flow,
physical access model, and the command or data access exposed by the requested tools:

```bash
r1-hermes hermes \
  --toolsets terminal,file \
  --allow-high-impact-toolsets
```

The high-impact gate covers toolsets such as `terminal`, `shell`, `file`/`filesystem`, browser or
desktop automation, smart-home/home automation, and vehicle/automotive controls. Do not enable them
to compensate for public exposure or unclear device ownership.

## Current bridge limits

The `r1-hermes hermes` command is the supported runtime today. It is a security-first standalone
bridge: Rabbit R1/OpenClaw connects over WebSocket, completes `connect` authentication, sends
`chat.send`, and `r1-hermes` starts `hermes chat --quiet --query ...` with
`asyncio.create_subprocess_exec`. It does not use a shell, does not pass gateway/device tokens on
the command line, and does not return Hermes stderr to the device.

What works now:

- Rabbit/OpenClaw-style pairing with a bearer gateway token and per-device token.
- Authenticated text `chat.send` requests after `connect`.
- Stable Hermes CLI continuation sessions per R1 `device.id` and `sessionKey`.
- Local `safe` toolset by default, with explicit `--toolsets` expansion and a separate
  high-impact override for host/file/automation control surfaces.
- Global and per-device in-flight caps before Hermes subprocess execution.
- Per-device/session `chat.send` dedupe by `idempotencyKey`, returning `BUSY_DUPLICATE` for an
  active duplicate and replaying the cached final or error event for a recent completed duplicate.
  If a mobile-network drop closes the socket after `chat.run_started`, an idempotent run can finish
  in the background; reconnecting with the same device ID, `sessionKey`, and `idempotencyKey` is
  deterministic. Clients should wait and retry on `BUSY_DUPLICATE`, stop retrying once a replayed
  final or error event arrives, and generate a new `idempotencyKey` only for a genuinely new user
  send.
  If the final event was computed but not delivered because the socket closed, the gateway caches
  that final/error event for the retry window. If the socket closed before the handler could start,
  or the request failed parsing, there is no cached result; retrying the same frame re-enters normal
  validation and may return the same safe parser error.
- Generic started/final/error chat events back to the active WebSocket.
- `chat.history` compatibility responses after authentication. History is intentionally unsupported
  in this standalone bridge: known and unknown `sessionKey` values both receive an empty
  `messages` array plus `status: "unsupported"`, `historySupported: false`, and `storage: "none"`.
  The gateway does not keep a transcript, prompt text, assistant replies, tokens, QR payloads, or
  auth headers for history replay. Reconnect continuity comes only from the stable Hermes CLI
  continuation name used by later `chat.send` calls, not from R1-visible history storage.

What does not work in the standalone bridge:

- In-process Hermes Gateway `_message_handler` routing.
- Gateway-owned platform registry/config loading for Rabbit R1.
- Gateway platform toolset resolution beyond the CLI `--toolsets` argument.
- Gateway send/proactive delivery queues for offline or never-activated sessions.
- Native media, attachment, STT, and TTS mapping.
- Full Hermes Gateway session, channel, and user semantics.
- R1-visible conversation transcript replay through `chat.history`.

## Native Hermes Gateway path

A native Rabbit R1 platform should reuse the authenticated R1 WebSocket boundary from this repo but
hand authenticated text to Hermes Gateway as a `MessageEvent`, then let Gateway process it through
its normal `_message_handler`. There are three viable integration paths.

Standalone bridge + `hermes chat`:

- Benefits: works today, keeps an isolated process boundary, has simple deployment, and requires no
  Hermes repo changes.
- Costs and risks: it is not a native Gateway platform; toolsets are CLI-only; Gateway send queue,
  media, and session semantics are unavailable.
- Recommended use: default operator runtime until native Gateway APIs are stable.

Hermes repo `gateway/platforms/r1_shim.py`:

- Benefits: direct access to `MessageEvent`, `_message_handler`, `send()`, Gateway config, and
  platform toolsets.
- Costs and risks: requires Hermes repo changes and release coordination; platform/session naming
  becomes part of Gateway state.
- Recommended use: first upstream proof once Hermes maintainers accept an R1 platform.

Plugin platform package:

- Benefits: lets R1 support ship outside Hermes core while using Gateway plugin loading.
- Costs and risks: depends on stable Hermes plugin hooks and version compatibility; packaging must
  preserve secret handling.
- Recommended use: long-term distribution if Hermes supports external platform plugins.

The prototype in `src/r1_hermes/native_gateway.py` covers the local side of that design without
taking a runtime dependency on the Hermes repository. `R1NativeGatewayAdapter` keeps the same
localhost/private-network defaults, token authentication, keyed device-token digest storage, rate limits, message
length limits, global/per-device concurrency limits, and generic error events as the standalone adapter. Its `R1GatewayMessageBridge`
converts `chat.send` into a dependency-free `R1GatewayMessageEvent` with these stable fields:

- `platform`: `rabbit_r1`
- `user_id` and `channel_id`: sanitized R1 `device.id`
- `session_id`: `r1:<device_id>:<session_key>`
- `source`: `rabbit_r1:<device_id>:<session_key>`
- `text`: the authenticated message text, excluded from `repr()`
- `metadata`: sanitized `device_id`, `session_key`, and configured platform toolsets after
  high-impact entries are removed unless explicitly allowlisted

The prototype `send_text()` method sends a final chat event only when an authenticated WebSocket has
activated the exact `device_id`/`session_key` by sending a `chat.send` in the current process. It
returns `False` instead of queuing or persisting when the socket is absent or closed. That behavior
keeps proactive delivery fail-closed until a Hermes-native adapter can define durable delivery and
offline semantics.

If this moves into Hermes core, the Gateway adapter should map the prototype fields to Hermes'
actual `MessageEvent` class, call the standard Gateway `_message_handler`, implement `send()` using
the active WebSocket map, and load R1 platform toolsets from Gateway config rather than CLI flags.
The allowed user policy should default to the authenticated `device.id`, and the runtime
`R1_HERMES_ALLOWED_DEVICE_IDS` / `--allowed-device-id` policy should stay aligned between the
standalone bridge and native adapter. Do not accept any `chat.send` before `connect`, do not broaden
the default bind address or log bearer material during migration, and keep high-impact platform
toolsets behind the same explicit allowlist.

## Migration notes

No external DB migration is needed for the current `r1-hermes` standalone bridge or the local
native prototype; both continue to use the local `devices.json` state file. The allowed-device
policy is runtime configuration only. Existing `devices.json` records remain valid on disk, but
records whose device IDs are not in the configured allowlist are rejected at reconnect until the
allowlist includes them or the operator revokes/removes them. On first successful device-token
reconnect, older unkeyed SHA-256 records are upgraded in place to keyed HMAC digests. The
`chat.history` compatibility contract stores no history, so it adds no local state-file migration
and no persistence compatibility plan.
The prototype uses platform name `rabbit_r1` and session ID `r1:<device_id>:<session_key>`. Moving
an existing CLI deployment to a future Hermes-native platform can change the Hermes
conversation/session identity because the standalone bridge currently uses
`hermes chat --continue r1-hermes-...`. Treat that as a conversation-continuity break unless the
Hermes-side migration explicitly aliases the old `r1-hermes-*` CLI continuation names to the new
Gateway session IDs.
