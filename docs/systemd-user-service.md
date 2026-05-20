# systemd user service

This template runs `r1-hermes hermes` as a systemd user service. Keep the service bound to the narrowest reachable address and keep bearer secrets in the environment file only.

Do not expose the raw gateway on the public Internet. Prefer one of these patterns:

- `127.0.0.1` with Tailscale Serve or a reverse proxy that enforces access controls
- a specific Tailscale host IP such as `100.x.y.z` when Rabbit R1 must connect directly over the tailnet

Avoid `0.0.0.0`; it is not a safe default for this bridge.

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

Do not redirect service output to a plain file unless that file has a retention policy and `0600` permissions. Prefer journald retention controls for log rotation:

```bash
journalctl --user --vacuum-time=14d
journalctl --user --vacuum-size=200M
```

Logs must not contain full gateway tokens, device tokens, QR payloads, or raw authorization headers. Treat unexpected secret material in logs as an incident and rotate the gateway token before reconnecting devices.

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

If a token or QR payload may have leaked, rotate the gateway token in `~/.config/r1-hermes/r1-hermes.env`, move the state directory aside so old device tokens are invalidated, and start again:

```bash
systemctl --user stop r1-hermes.service
mv ~/.local/state/r1-hermes ~/.local/state/r1-hermes.revoked.$(date +%Y%m%d%H%M%S) 2>/dev/null || true
mkdir -m 700 -p ~/.local/state/r1-hermes
$EDITOR ~/.config/r1-hermes/r1-hermes.env
systemctl --user start r1-hermes.service
```

After rollback or token rotation, regenerate the Rabbit R1 QR payload and probe the gateway before pairing.
