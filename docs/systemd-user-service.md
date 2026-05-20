# systemd user service

This template runs `r1-hermes hermes` as a systemd user service. Keep the service bound to the narrowest reachable address and keep bearer secrets in the environment file only.

Do not expose the raw gateway on the public Internet. Prefer one of these patterns:

- `127.0.0.1` with Tailscale Serve or a reverse proxy that enforces access controls
- a specific Tailscale host IP such as `100.x.y.z` when Rabbit R1 must connect directly over the tailnet

Wildcard bind hosts such as `0.0.0.0` and `::` fail closed by default. Avoid them; they are not
safe defaults for this bridge. If a reviewed private network boundary genuinely requires a wildcard
bind, add `--allow-public-bind` in an `ExecStart` override or set
`R1_HERMES_ALLOW_PUBLIC_BIND=1` in the env file after documenting why a concrete Tailscale/LAN IP
cannot be used.

## Install

Install the package for the user that will run the service:

```bash
python -m pip install --user -e '.[qr]'
```

Install the unit and create the env file:

```bash
mkdir -p ~/.config/systemd/user ~/.config/r1-hermes ~/.local/state/r1-hermes
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
R1_HERMES_GLOBAL_CONCURRENCY=2
R1_HERMES_PER_DEVICE_CONCURRENCY=1
```

The concurrency defaults are sized for one personal Rabbit R1. In a multi-device deployment, raise
`R1_HERMES_GLOBAL_CONCURRENCY` only to the number of simultaneous Hermes subprocesses the host can
run comfortably. Keep `R1_HERMES_PER_DEVICE_CONCURRENCY=1` unless one trusted device is explicitly
allowed to consume multiple slots.

Leave `R1_HERMES_ALLOW_PUBLIC_BIND` unset for localhost and concrete IP binds. Setting it to `1`
allows all-interface binds and should be treated as an explicit exposure acknowledgement, not
routine configuration.

Keep `R1_HERMES_TOOLSETS=safe` for normal R1 use. `safe,web` is the usual explicit expansion when
web access is needed. High-impact values such as `terminal`, `shell`, `file`/`filesystem`,
browser/desktop automation, smart-home, or vehicle controls fail closed unless the service env file
also sets `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1` after a deployment review.

Leave HTTP health checks local-only unless a reviewed supervisor must reach `/healthz` from another
host. The default response is only `{"ok": true}` and does not include paired-device counts. If a
private, access-controlled monitoring path genuinely needs remote health checks, set
`R1_HERMES_ALLOW_REMOTE_HEALTH=1`. Add `R1_HERMES_HEALTH_DIAGNOSTICS=1` only when the paired count is
needed for local diagnostics, not for broadly reachable probes.

If `r1-hermes` is not installed at `~/.local/bin/r1-hermes`, override `ExecStart` with the exact absolute path and keep the command shell-free:

```bash
systemctl --user edit r1-hermes.service
```

```ini
[Service]
ExecStart=
ExecStart=/absolute/path/to/r1-hermes hermes --host ${R1_HERMES_HOST} --port ${R1_HERMES_PORT} --state-dir %h/.local/state/r1-hermes --ready-file %t/r1-hermes/ready --toolsets ${R1_HERMES_TOOLSETS} --timeout ${R1_HERMES_TIMEOUT} --global-concurrency ${R1_HERMES_GLOBAL_CONCURRENCY} --per-device-concurrency ${R1_HERMES_PER_DEVICE_CONCURRENCY}
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

`r1-hermes probe` reads `R1_HERMES_GATEWAY_TOKEN` from the environment. Source the env file in a private shell when probing, and do not echo the token:

```bash
set -a
. ~/.config/r1-hermes/r1-hermes.env
set +a
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
