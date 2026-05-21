# systemd user service

This template runs `r1-hermes hermes` as a systemd user service. Keep the service bound to the narrowest reachable address and keep bearer secrets in the environment file only.

Do not expose the raw cleartext gateway on the public Internet. Prefer one of these patterns:

- `127.0.0.1` with Tailscale Serve or a reverse proxy that enforces access controls
- a specific Tailscale host IP such as `100.x.y.z` when Rabbit R1 must connect directly over the tailnet
- a reviewed public IP/hostname with native TLS enabled, advertised as `wss://`, when proxyless Rabbit R1 access is required

Wildcard bind hosts such as `0.0.0.0` and `::` fail closed by default. Avoid them; they are not
safe defaults for this bridge. If a reviewed private network boundary genuinely requires a wildcard
bind, add `--allow-public-bind` in an `ExecStart` override or set
`R1_HERMES_ALLOW_PUBLIC_BIND=1` in the env file after documenting why a concrete Tailscale/LAN IP
cannot be used.

## Install

Install the package for the user that will run the service:

```bash
python -m pip install --user '.[qr]'
```

Install the unit and create the env file:

```bash
mkdir -p ~/.config/systemd/user ~/.config/r1-hermes
cp packaging/systemd/r1-hermes.service ~/.config/systemd/user/r1-hermes.service
cp packaging/systemd/r1-hermes.env.example ~/.config/r1-hermes/r1-hermes.env
chmod 600 ~/.config/r1-hermes/r1-hermes.env
```

Generate a gateway token and edit only the env file:

```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
$EDITOR ~/.config/r1-hermes/r1-hermes.env
```

For local-only operation, keep:

```ini
R1_HERMES_HOST=127.0.0.1
R1_HERMES_PORT=18789
R1_HERMES_GLOBAL_CONCURRENCY=2
R1_HERMES_PER_DEVICE_CONCURRENCY=1
```

For direct tailnet operation, use the host's Tailscale IP instead of a wildcard bind:

```ini
R1_HERMES_HOST=100.x.y.z
R1_HERMES_PORT=18789
R1_HERMES_ALLOWED_DEVICE_IDS=r1-known-device-id
R1_HERMES_GLOBAL_CONCURRENCY=2
R1_HERMES_PER_DEVICE_CONCURRENCY=1
```

The concurrency defaults are sized for one personal Rabbit R1. In a multi-device deployment, raise
`R1_HERMES_GLOBAL_CONCURRENCY` only to the number of simultaneous Hermes subprocesses the host can
run comfortably. Keep `R1_HERMES_PER_DEVICE_CONCURRENCY=1` unless one trusted device is explicitly
allowed to consume multiple slots.

When the intended R1 ID is known, set `R1_HERMES_ALLOWED_DEVICE_IDS` in the env file before normal
service operation. It accepts comma, space, or newline-separated IDs. If you must learn the ID from
first pairing, leave the variable unset only on a private boundary, pair once, record the intended
ID from local state, use sanitized audit logs only for correlation, then restart the service with
the allowlist set.

Leave `R1_HERMES_ALLOW_PUBLIC_BIND` unset for localhost and concrete IP binds. Setting it to `1`
allows all-interface binds and should be treated as an explicit exposure acknowledgement, not
routine configuration.

For proxyless public operation, terminate TLS inside `r1-hermes` itself instead of advertising
cleartext `ws://` on a public address. Use a certificate whose SAN covers the hostname or IP that
Rabbit R1 will connect to, set the TLS paths in the env file, and generate pairing payloads with
`--protocol wss`:

```ini
R1_HERMES_HOST=66.94.115.69
R1_HERMES_PORT=18789
R1_HERMES_ALLOW_PUBLIC_BIND=1
R1_HERMES_TLS_CERT_FILE=/etc/letsencrypt/live/r1-hermes.example.com/fullchain.pem
R1_HERMES_TLS_KEY_FILE=/etc/letsencrypt/live/r1-hermes.example.com/privkey.pem
R1_HERMES_TOOLSETS=safe
R1_HERMES_GLOBAL_CONCURRENCY=2
R1_HERMES_PER_DEVICE_CONCURRENCY=1
```

```bash
r1-hermes probe \
  --url wss://r1-hermes.example.com:18789/ \
  --message 'Reply with OK from Hermes'

r1-hermes qr \
  --host r1-hermes.example.com \
  --port 18789 \
  --protocol wss \
  --output ~/.local/state/r1-hermes/pairing.png
```

The QR payload contains a bearer token. Do not print it, paste it into chats, or keep old PNGs after
pairing; rotate the gateway token if the QR is exposed.

For Tailscale Serve or a reverse proxy, keep the service env file on localhost:

```ini
R1_HERMES_HOST=127.0.0.1
R1_HERMES_PORT=18789
```

The external address belongs to Tailscale Serve, Caddy, nginx, or another reviewed proxy. Do not
change the systemd service to `0.0.0.0` to make a proxy work.

Keep `R1_HERMES_TOOLSETS=safe` for normal R1 use. `safe,web` is the usual explicit expansion when
web access is needed. High-impact values such as `terminal`, `shell`, `file`/`filesystem`,
browser/desktop automation, smart-home, or vehicle controls fail closed unless the service env file
also sets `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1` after a deployment review.

Before `systemctl --user restart r1-hermes.service`, run `r1-hermes doctor` with the env file loaded
so the effective R1 toolsets are compared with the configured Slack-equivalent bundle. For
safe/minimal service mode, `R1_HERMES_TOOLSETS=safe` is expected to warn about Slack parity but exit
zero. For intentionally reviewed Slack-equivalent service mode, set:

```ini
R1_HERMES_TOOLSETS=safe,web,terminal,file
R1_HERMES_SLACK_EQUIVALENT_TOOLSETS=safe,web,terminal,file
R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1
```

Then run `r1-hermes doctor --require-slack-equivalent-toolsets` and restart only if it reports no
failures. Adjust `R1_HERMES_SLACK_EQUIVALENT_TOOLSETS` only when the actual Slack deployment uses a
different reviewed bundle.

Leave HTTP health checks local-only unless a reviewed supervisor must reach `/healthz` from another
host. The default response is only `{"ok": true}` and does not include paired-device counts. If a
private, access-controlled monitoring path genuinely needs remote health checks, set
`R1_HERMES_ALLOW_REMOTE_HEALTH=1`. Add `R1_HERMES_HEALTH_DIAGNOSTICS=1` only when the paired count is
needed for local diagnostics, not for broadly reachable probes.

## Write-path assumptions

The packaged unit uses `ProtectSystem=strict`, `ProtectHome=tmpfs`, `StateDirectory=r1-hermes`,
`RuntimeDirectory=r1-hermes`, `BindReadOnlyPaths`, and `BindPaths` so the service sees only the home
subtrees it needs. It also blocks hostname changes, realtime scheduling, namespace creation, and
high-risk syscall groups. It deliberately avoids `WorkingDirectory=%h`; the working directory is
`%S/r1-hermes`, which is the systemd user state directory for this service. On a default Linux user
manager, `%S/r1-hermes` usually resolves to `~/.local/state/r1-hermes`.

The service expects these paths:

- `%h/.config/r1-hermes/r1-hermes.env`: readable env file. systemd reads this before the service
  sandbox is applied. Keep `R1_HERMES_GATEWAY_TOKEN` here only; do not add token literals to the unit
  or drop-ins.
- `%S/r1-hermes`: writable `r1-hermes` state directory for paired-device metadata and the local HMAC
  key. systemd creates it through `StateDirectory=r1-hermes` with `StateDirectoryMode=0700`.
- `%t/r1-hermes/ready`: writable runtime readiness file. systemd creates `%t/r1-hermes` through
  `RuntimeDirectory=r1-hermes` with `RuntimeDirectoryMode=0700`, and removes it when the user service
  stops.
- `%h/.local/bin` and `%h/.local/lib`: read-only user install paths for a normal
  `python -m pip install --user` installation. If your `r1-hermes` executable or Python environment
  lives elsewhere, add that exact read-only path in a drop-in. Editable installs whose `.pth` files
  point to a source checkout under another home path need an explicit read-only bind for that source
  tree, or a non-editable reinstall.
- `%h/.hermes`: writable Hermes Agent home. The Hermes CLI commonly stores config, provider auth,
  sessions, checkpoints, and Hermes logs there while `hermes chat --continue ...` runs. This is the
  remaining broad home exception; keep it to this subtree unless the local Hermes installation uses a
  different documented home.
- journald: normal `r1-hermes` service stdout/stderr and audit events. journald storage is managed by
  the user manager, not by a writable path inside the unit sandbox.

If your Hermes installation stores its config or sessions somewhere other than `%h/.hermes`, add a
drop-in that replaces `BindPaths` with the exact required directory. If your systemd version or Linux
kernel cannot use `ProtectHome=tmpfs`, the less restrictive fallback is `ProtectHome=read-only` plus
`ReadWritePaths` for `%S/r1-hermes`, `%t/r1-hermes`, and the exact Hermes home. Do not loosen the
service to writable `$HOME`, do not move secrets into `ExecStart`, and keep the command shell-free.

If `r1-hermes` is not installed at `~/.local/bin/r1-hermes`, override `ExecStart` with the exact absolute path and keep the command shell-free:

```bash
systemctl --user edit r1-hermes.service
```

```ini
[Service]
ExecStart=
ExecStart=/absolute/path/to/r1-hermes hermes --host ${R1_HERMES_HOST} --port ${R1_HERMES_PORT} --state-dir %S/r1-hermes --ready-file %t/r1-hermes/ready --toolsets ${R1_HERMES_TOOLSETS} --timeout ${R1_HERMES_TIMEOUT} --global-concurrency ${R1_HERMES_GLOBAL_CONCURRENCY} --per-device-concurrency ${R1_HERMES_PER_DEVICE_CONCURRENCY}
```

## Enable

Reload systemd and start the user service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now r1-hermes.service
```

To keep the service running after logout on a dedicated host, enable lingering for that user:

```bash
loginctl enable-linger "$USER"
```

## Status

Check the unit and the ready file:

```bash
systemctl --user status r1-hermes.service
test -s "${XDG_RUNTIME_DIR}/r1-hermes/ready"
cat "${XDG_RUNTIME_DIR}/r1-hermes/ready"
```

The service uses `--ready-file %t/r1-hermes/ready`; systemd removes the old file before each start, and `r1-hermes` writes it only after the gateway starts listening.

Validate the installed or packaged unit when `systemd-analyze` is available:

```bash
systemd-analyze --user verify ~/.config/systemd/user/r1-hermes.service
systemd-analyze --user verify packaging/systemd/r1-hermes.service
```

For local HTTP readiness, `/healthz` is intentionally minimal:

```bash
curl --fail --silent http://127.0.0.1:18789/healthz
```

Expected output:

```json
{"ok": true}
```

Do not depend on `/healthz` for paired-device counts unless diagnostics were explicitly enabled.

Run an end-to-end health check before scanning a QR code with Rabbit R1:

```bash
r1-hermes probe \
  --url ws://127.0.0.1:18789/ \
  --message 'Reply with OK from Hermes'
```

For Tailscale direct binding, probe the advertised address:

```bash
r1-hermes probe \
  --url ws://100.x.y.z:18789/ \
  --message 'Reply with OK from Hermes'
```

## Tailscale Serve with localhost bind

This is the preferred systemd pattern when the Rabbit R1 can reach a tailnet HTTPS service name.
Keep `R1_HERMES_HOST=127.0.0.1` in `~/.config/r1-hermes/r1-hermes.env`, restart the user service,
then publish the loopback listener through Tailscale Serve:

```bash
systemctl --user restart r1-hermes.service
tailscale serve --bg --https=443 127.0.0.1:18789
tailscale serve status
```

If your installed Tailscale CLI requires an explicit HTTP upstream URL, use:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:18789
```

Check that the backend remains local-only:

```bash
ss -ltnp | grep ':18789'
```

Expected: `r1-hermes` is listening on `127.0.0.1:18789` or `[::1]:18789`, not
`0.0.0.0:18789` or `[::]:18789`.

From an allowed tailnet client, source the env file in a private shell and probe the public
Tailscale HTTPS URL:

```bash
set -a
. ~/.config/r1-hermes/r1-hermes.env
set +a

r1-hermes probe \
  --url wss://r1-hermes-host.tailnet-name.ts.net/ \
  --message 'Reply with OK from Hermes'
```

From an untrusted network or a tailnet identity not allowed by ACLs, verify the same URL fails to
connect or returns an access error. It must not return HTTP 200:

```bash
curl --fail --silent --show-error https://r1-hermes-host.tailnet-name.ts.net/healthz
```

Generate the QR for the Tailscale HTTPS service name and delete it after pairing:

```bash
r1-hermes qr \
  --host r1-hermes-host.tailnet-name.ts.net \
  --port 443 \
  --protocol wss \
  --output ./r1-hermes-secret.png
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

Do not use Tailscale Funnel for this service unless a human explicitly approves public-Internet
exposure and compensating controls.

## Reverse proxy with localhost bind

Use this pattern when a TLS hostname outside the tailnet must front the gateway. Keep the systemd
service bound to `127.0.0.1`, then enforce mTLS or a narrow source IP allowlist at the proxy. This
Caddyfile requires client certificates:

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

If mTLS is not available, use an explicit allowlist and keep it narrow:

```caddyfile
r1.example.com {
    @allowed remote_ip 198.51.100.10 203.0.113.0/24 2001:db8:1234::/48
    @not_allowed not remote_ip 198.51.100.10 203.0.113.0/24 2001:db8:1234::/48

    respond @not_allowed "forbidden" 403
    reverse_proxy @allowed 127.0.0.1:18789
}
```

If Caddy is behind another trusted proxy, configure trusted proxy handling before relying on
`remote_ip`.

Verify the backend bind and the trusted external path:

```bash
ss -ltnp | grep ':18789'
curl --fail --silent http://127.0.0.1:18789/healthz
r1-hermes probe --url wss://r1.example.com/ --message 'Reply with OK from Hermes'
```

From an untrusted network, the external URL must fail TLS client-certificate authentication, return
403, time out, or otherwise fail closed. It must not return HTTP 200:

```bash
curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}\n' \
  https://r1.example.com/healthz
```

Generate the QR for the proxy hostname, not the loopback backend:

```bash
r1-hermes qr \
  --host r1.example.com \
  --port 443 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Use `ws://` in the QR only when Rabbit R1 connects directly to the raw `r1-hermes` listener over a
reviewed private, non-TLS path such as a concrete Tailscale IP or isolated LAN IP. Use `wss://`
when Tailscale Serve, Caddy, nginx, or another reverse proxy terminates TLS and forwards to
`127.0.0.1:18789`. Do not mark a plain `ws://` backend as `wss://`; the QR protocol must match the
URL Rabbit R1 actually dials, not the proxy's upstream URL. Do not use `--print-payload` for these
deployment flows.

`r1-hermes probe` reads `R1_HERMES_GATEWAY_TOKEN` from the environment. Source the env file in a private shell when probing, and do not echo the token:

```bash
set -a
. ~/.config/r1-hermes/r1-hermes.env
set +a
```

## Permission-related startup failures

If the service fails immediately after a sandboxing change, check the unit status first:

```bash
systemctl --user status r1-hermes.service
journalctl --user-unit r1-hermes.service --since today -o cat
```

Common permission failures and fixes:

- `status=226/NAMESPACE`, `EXIT_STATE_DIRECTORY`, or errors creating `%S/r1-hermes`: verify the user
  manager supports `StateDirectory=` and that `$XDG_STATE_HOME` or `~/.local/state` is owned by the
  service user.
- `Failed to set up mount namespacing` or `ProtectHome=tmpfs` errors: verify unprivileged user
  namespaces are enabled for user services on this host. If they cannot be enabled, use a local
  drop-in with `ProtectHome=read-only` and a minimal `ReadWritePaths` fallback for `%S/r1-hermes`,
  `%t/r1-hermes`, and the exact Hermes home.
- `Permission denied` for `%t/r1-hermes/ready`: keep `RuntimeDirectory=r1-hermes`,
  `RuntimeDirectoryMode=0700`, and `--ready-file %t/r1-hermes/ready` together. Do not point the ready
  file at `/tmp` or an arbitrary home path in the packaged service.
- Hermes starts manually but fails under systemd: confirm Hermes can read and write its configured
  home. The default bind allowlist includes `%h/.hermes`; add the exact alternate Hermes home to
  `BindPaths` if your installation uses one.
- `r1-hermes` is missing or cannot import packages: install it for the same user or override
  `ExecStart` to the absolute executable path. If the executable lives outside `%h/.local/bin`, keep
  the target path readable and executable without making all of `$HOME` writable.
- The env file is rejected or not found: keep it at `%h/.config/r1-hermes/r1-hermes.env`, owned by the
  service user, with `0600` permissions. Store the gateway token only in that file.

After changing a unit or drop-in, reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart r1-hermes.service
```

## Logs

Read logs from journald:

```bash
journalctl --user-unit r1-hermes.service --since today
journalctl --user-unit r1-hermes.service -f
```

Structured audit events are emitted as single-line JSON through the `r1_hermes.audit` logger. They
use event names such as `auth.success`, `auth.failure`, `auth.parser_error`, `rate_limited`,
`busy_rejected`, `chat.run_started`, `chat.run_final`, `chat.run_error`,
`hermes.subprocess_failed`, `device.revoke`, `device.revoke_all`, and `device.cleanup`. Filter them with:

```bash
journalctl --user-unit r1-hermes.service --since today -o cat | grep '"event"'
```

The events intentionally use hashed identifiers and counts instead of raw device IDs, bearer tokens,
QR payloads, authorization headers, full prompts, or Hermes stderr. `INFO` records successful
lifecycle events, `WARNING` records rejected requests and Hermes subprocess exits, and `ERROR`
records authenticated chat runs that failed behind the generic device-facing error boundary. The
default service logs at `INFO`; set `R1_HERMES_LOG_LEVEL` in the env file if you need a different
Python logging threshold.

Do not redirect service output to a plain file unless that file has a retention policy and `0600` permissions. Prefer journald retention controls for log rotation:

```bash
journalctl --user --vacuum-time=14d
journalctl --user --vacuum-size=200M
```

Logs must not contain full gateway tokens, device tokens, QR payloads, or raw authorization headers. Treat unexpected secret material in logs as an incident and rotate the gateway token before reconnecting devices.

## Token rotation and revoke-all

For a leaked gateway token, QR payload, QR PNG, or unknown device-token exposure, stop the service,
rotate the env-file token, and revoke every paired device in the same local operation. The command
prints the env-file path and affected device IDs only; it does not print the new gateway token.

Preview first:

```bash
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env \
  --dry-run
```

Apply the rotation while the gateway is stopped:

```bash
systemctl --user stop r1-hermes.service
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env
systemctl --user start r1-hermes.service
```

Source the updated env file in a private shell and probe the restarted gateway before scanning a new
QR. Do not echo the token:

```bash
set -a
. ~/.config/r1-hermes/r1-hermes.env
set +a

r1-hermes probe \
  --url ws://127.0.0.1:18789/ \
  --message 'Reply with OK from Hermes'
```

For a Tailscale direct bind, probe the concrete advertised address instead of `127.0.0.1`. Then
generate a fresh QR PNG with the same host/port, scan it only on the intended Rabbit R1, and delete
the PNG after pairing:

```bash
r1-hermes qr \
  --host 100.x.y.z \
  --port 18789 \
  --protocol ws \
  --output ./r1-hermes-secret.png
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

To invalidate all paired devices without rotating the gateway token, run:

```bash
r1-hermes revoke --state-dir ~/.local/state/r1-hermes --all
```

Use `--dry-run` to list the affected device IDs before rewriting the state file. The revoke command
is safe to repeat when no devices are paired.

## Rollback

Stop and disable the service:

```bash
systemctl --user disable --now r1-hermes.service
```

Restore a known-good unit or remove the local template:

```bash
cp /path/to/known-good/r1-hermes.service ~/.config/systemd/user/r1-hermes.service
systemctl --user daemon-reload
```

If a token or QR payload may have leaked during rollback, use the rotation workflow above instead
of keeping the old state. For a non-secret rollback that only restores the service unit, start the
service again after the known-good unit is in place:

```bash
systemctl --user start r1-hermes.service
```

After rollback or token rotation, regenerate the Rabbit R1 QR payload and probe the gateway before pairing.
