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

## Network pairing

Pick the narrowest reachable address. Tailscale is preferred over broad LAN exposure.

```bash
r1-hermes hermes --host 100.x.y.z --port 18789
r1-hermes qr --host 100.x.y.z --port 18789 --protocol ws --output ./r1-hermes-secret.png
```

The gateway starts at most two Hermes subprocess-backed chat runs at once by default, and at most
one per authenticated device ID:

```bash
r1-hermes hermes \
  --host 100.x.y.z \
  --port 18789 \
  --global-concurrency 2 \
  --per-device-concurrency 1
```

The same settings can be supplied through `R1_HERMES_GLOBAL_CONCURRENCY` and
`R1_HERMES_PER_DEVICE_CONCURRENCY`. Keep `2`/`1` for one personal Rabbit R1. For multiple trusted
devices, increase only the global cap to the number of concurrent Hermes subprocesses the host can
comfortably run; keep the per-device cap low unless one device is intentionally allowed to occupy
several slots. Requests over either cap receive `BUSY` before Hermes is invoked.

The gateway rejects wildcard bind hosts such as `0.0.0.0`, `::`, and numeric aliases for all
interfaces unless you explicitly acknowledge the exposure with `--allow-public-bind` or
`R1_HERMES_ALLOW_PUBLIC_BIND=1`. Treat that opt-in as an exception for a reviewed private network
boundary only. Prefer `127.0.0.1` with Tailscale Serve, `127.0.0.1` behind a reverse proxy with
mTLS or IP allowlisting, or a concrete Tailscale/LAN IP.

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
```

These limits protect the handshake path but are not a substitute for Tailscale, firewall rules,
mTLS, or IP allowlisting.

For compatibility debugging, `--dump-frames` prints sanitized WebSocket frames to stderr. Auth token
fields and text/audio content fields are redacted before printing; still keep the output local until
you have reviewed it.

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --message 'Reply with OK from Hermes' \
  --dump-frames
```

The default probe uses the standard `connect` handshake. To smoke-test the OpenClaw/Rabbit
`gateway.connect` variant and its compatibility acknowledgement events, run:

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --connect-method gateway.connect \
  --message 'Reply with OK from Hermes'
```

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
  timestamps, audio/image payloads, and unrelated UI state

Redaction is mandatory before committing or pasting captures anywhere. Replace gateway tokens,
device tokens, QR secrets, API keys, cookies, bearer headers, and raw auth headers with obvious
dummy values such as `DUMMY_GATEWAY_TOKEN_DO_NOT_USE` or `DUMMY_DEVICE_TOKEN_DO_NOT_USE`. Do not
commit raw packet captures, screenshots containing QR codes, or terminal logs that include
unreviewed frames.

## Tool access

Default toolset is `safe`. Expand deliberately:

```bash
r1-hermes hermes --toolsets safe,web
```

Do not enable high-impact toolsets such as `terminal`, `file`, or smart-home controls until the network boundary, pairing flow, and physical access model are reviewed.

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
- Local `safe` toolset by default, with explicit `--toolsets` expansion.
- Global and per-device in-flight caps before Hermes subprocess execution.
- Generic started/final/error chat events back to the active WebSocket.

What does not work in the standalone bridge:

- In-process Hermes Gateway `_message_handler` routing.
- Gateway-owned platform registry/config loading for Rabbit R1.
- Gateway platform toolset resolution beyond the CLI `--toolsets` argument.
- Gateway send/proactive delivery queues for offline or never-activated sessions.
- Native media, attachment, STT, and TTS mapping.
- Full Hermes Gateway session, channel, and user semantics.

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
- `metadata`: sanitized `device_id`, `session_key`, and configured platform toolsets

The prototype `send_text()` method sends a final chat event only when an authenticated WebSocket has
activated the exact `device_id`/`session_key` by sending a `chat.send` in the current process. It
returns `False` instead of queuing or persisting when the socket is absent or closed. That behavior
keeps proactive delivery fail-closed until a Hermes-native adapter can define durable delivery and
offline semantics.

If this moves into Hermes core, the Gateway adapter should map the prototype fields to Hermes'
actual `MessageEvent` class, call the standard Gateway `_message_handler`, implement `send()` using
the active WebSocket map, and load R1 platform toolsets from Gateway config rather than CLI flags.
The allowed user policy should default to the authenticated `device.id`, with an explicit allowlist
for known device IDs when the deployment needs one. Do not accept any `chat.send` before `connect`,
and do not broaden the default bind address or log bearer material during migration.

## Migration notes

No external DB migration is needed for the current `r1-hermes` standalone bridge or the local
native prototype; both continue to use the local `devices.json` state file. On first successful
device-token reconnect, older unkeyed SHA-256 records are upgraded in place to keyed HMAC digests.
The prototype uses platform name `rabbit_r1` and session ID `r1:<device_id>:<session_key>`. Moving
an existing CLI deployment to a future Hermes-native platform can change the Hermes
conversation/session identity because the standalone bridge currently uses
`hermes chat --continue r1-hermes-...`. Treat that as a conversation-continuity break unless the
Hermes-side migration explicitly aliases the old `r1-hermes-*` CLI continuation names to the new
Gateway session IDs.
