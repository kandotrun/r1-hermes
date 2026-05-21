# Public native WSS runbook

This runbook is for the exception case where Rabbit R1 must connect directly to a public
`r1-hermes` listener over native TLS, without Tailscale Serve, Caddy, nginx, or another reverse
proxy. It uses a trusted Let's Encrypt certificate, `wss://`, device allowlisting, and local
systemd user service hardening.

Prefer Tailscale or private networking when you control the Rabbit R1's network path. Public native
WSS is appropriate only after reviewing the public exposure, firewall boundary, gateway-token
handling, device allowlist, toolsets, and rollback plan. Keep the default Hermes toolset at `safe`.
Do not enable terminal, file, smart-home, browser/desktop automation, or other high-impact toolsets
for a public R1 endpoint unless a human explicitly accepts that risk.

## Scope and placeholders

Use placeholders in public notes and issue comments:

- `<PUBLIC_IP>`: the VPS public address that will receive Rabbit R1 traffic.
- `<R1_HOSTNAME>`: the DNS name Rabbit R1 will dial, for example a custom domain or an `sslip.io`
  hostname that resolves to `<PUBLIC_IP>`.
- `<SERVICE_USER>`: the Linux user that runs the systemd user service.
- `<INTENDED_R1_DEVICE_ID>`: the locally recorded Rabbit R1 `device.id`; never paste a real value
  into public issues, pull requests, screenshots, or logs.

Never paste raw gateway tokens, device tokens, QR payload JSON, QR screenshots, raw authorization
headers, or real device IDs into public systems. Commands below either read secrets from private
files or use placeholders.

## 1. Prefer a private boundary first

Use one of these instead of public native WSS when feasible:

- `127.0.0.1` behind Tailscale Serve with tailnet ACLs.
- a concrete Tailscale IP such as `100.x.y.z` with `ws://` on the tailnet only.
- `127.0.0.1` behind a reverse proxy that enforces mTLS or a narrow source IP allowlist.

See [`running.md`](running.md) and [`systemd-user-service.md`](systemd-user-service.md) for those
patterns. Continue with this runbook only when proxyless public reachability is required.

## 2. Choose the hostname

Use a custom domain when possible. Create an `A` record, and an `AAAA` record if you use IPv6, that
points to the VPS address:

```text
<R1_HOSTNAME>. 300 IN A <PUBLIC_IP>
```

For throwaway or lab VPS hosts, `sslip.io` can provide a DNS name without editing a zone file. Pick
the hostname form documented by `sslip.io` for your address, confirm it resolves to the intended
VPS, and use that exact hostname everywhere: certificate issuance, TLS SNI checks, probe URL, and QR
generation.

```bash
R1_HOSTNAME="<PUBLIC_IP>.sslip.io"
dig +short "$R1_HOSTNAME"
```

Do not issue or advertise a certificate for a name that Rabbit R1 will not actually dial. The
certificate subject alternative name must cover `<R1_HOSTNAME>`, not `127.0.0.1` and not the
loopback backend from a different deployment mode.

## 3. Lock down the network boundary

Open only the ports needed for certificate issuance and the native WSS listener. On hosts using
`ufw`, the shape is:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 18789/tcp
sudo ufw status verbose
```

Use the cloud firewall as the primary boundary when your VPS provider offers one. If you know the
Rabbit R1 egress address or a narrow egress range, restrict port `18789/tcp` there as well. If you
cannot restrict the source address, the gateway token and `R1_HERMES_ALLOWED_DEVICE_IDS` become the
critical controls; do not run first-pairing without an allowlist on this public boundary.

## 4. Install and verify the service locally

Install `r1-hermes` for the service user, create the env file, and keep it private:

```bash
python -m pip install --user '.[qr]'
mkdir -p ~/.config/systemd/user ~/.config/r1-hermes
cp packaging/systemd/r1-hermes.service ~/.config/systemd/user/r1-hermes.service
cp packaging/systemd/r1-hermes.env.example ~/.config/r1-hermes/r1-hermes.env
chmod 600 ~/.config/r1-hermes/r1-hermes.env
```

Generate the gateway token in a private terminal and place it only in the env file. Avoid shell
history and screen sharing. Do not paste the generated value into GitHub, chat, or service unit
drop-ins.

```bash
python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
$EDITOR ~/.config/r1-hermes/r1-hermes.env
```

Start with localhost smoke checks before opening public WSS:

```bash
systemctl --user daemon-reload
systemctl --user enable --now r1-hermes.service
systemctl --user status r1-hermes.service
r1-hermes probe --url ws://127.0.0.1:18789/ --message 'Reply with OK from Hermes'
```

The probe prints only the assistant response; it must not print the issued device token.

## 5. Get a Let's Encrypt certificate

Use `certbot certonly --standalone` when no web server is already bound to port 80. Replace
`<R1_HOSTNAME>` with the real custom domain or `sslip.io` hostname. These examples must be run only
on the VPS that `<R1_HOSTNAME>` resolves to.

```bash
sudo certbot certonly --standalone -d <R1_HOSTNAME>
sudo certbot certificates -d <R1_HOSTNAME>
```

If port 80 cannot be opened, use a DNS-01 capable certbot plugin for your DNS provider instead of
falling back to a self-signed certificate. Rabbit R1 must trust the certificate chain.

Create a dedicated group and copy the live certificate material into a directory readable by the
systemd user service. Copying avoids granting the service user broad traversal access to
`/etc/letsencrypt`.

```bash
sudo groupadd --system r1-hermes-certs
sudo usermod -a -G r1-hermes-certs <SERVICE_USER>
sudo install -d -m 0750 -o root -g r1-hermes-certs /etc/r1-hermes/tls/<R1_HOSTNAME>
sudo install -m 0640 -o root -g r1-hermes-certs \
  /etc/letsencrypt/live/<R1_HOSTNAME>/fullchain.pem \
  /etc/r1-hermes/tls/<R1_HOSTNAME>/fullchain.pem
sudo install -m 0640 -o root -g r1-hermes-certs \
  /etc/letsencrypt/live/<R1_HOSTNAME>/privkey.pem \
  /etc/r1-hermes/tls/<R1_HOSTNAME>/privkey.pem
sudo chgrp r1-hermes-certs /etc/r1-hermes/tls/<R1_HOSTNAME>/*.pem
sudo chmod 640 /etc/r1-hermes/tls/<R1_HOSTNAME>/*.pem
```

Log out and back in, or restart the user manager, so `<SERVICE_USER>` receives the new group
membership before the service tries to read the private key:

```bash
id
systemctl --user restart r1-hermes.service
```

## 6. Configure native TLS and allowlist

Edit `~/.config/r1-hermes/r1-hermes.env` for public native WSS. Bind to the concrete public address
when the VPS has one; use a wildcard bind only when the host or provider makes a concrete bind
impossible and the firewall boundary has been reviewed.

```ini
R1_HERMES_HOST=<PUBLIC_IP>
R1_HERMES_PORT=18789
R1_HERMES_TLS_CERT_FILE=/etc/r1-hermes/tls/<R1_HOSTNAME>/fullchain.pem
R1_HERMES_TLS_KEY_FILE=/etc/r1-hermes/tls/<R1_HOSTNAME>/privkey.pem
R1_HERMES_TOOLSETS=safe
R1_HERMES_GLOBAL_CONCURRENCY=2
R1_HERMES_PER_DEVICE_CONCURRENCY=1
R1_HERMES_ALLOWED_DEVICE_IDS=<INTENDED_R1_DEVICE_ID>
```

If a wildcard bind is unavoidable, add this only after documenting why a concrete public IP cannot
be used:

```ini
R1_HERMES_HOST=::
R1_HERMES_ALLOW_PUBLIC_BIND=1
```

Do not paste raw device IDs into public issues. For an already known Rabbit R1, store the exact
value only in the private env file. For multiple intended devices, use comma, space, or
newline-separated values in `R1_HERMES_ALLOWED_DEVICE_IDS`.

If the intended `device.id` is not known yet, do not learn it on public native WSS. Pair once on a
private boundary such as Tailscale Serve or a concrete Tailscale IP, record the intended ID locally
from `devices.json` or correlate the sanitized `device_id_hash` in local audit logs, revoke any
unintended records, then restart public WSS with `R1_HERMES_ALLOWED_DEVICE_IDS` set.

Restart and verify the service:

```bash
systemctl --user daemon-reload
systemctl --user restart r1-hermes.service
systemctl --user status r1-hermes.service
ss -ltnp | grep ':18789'
```

Expected: the listener is on `<PUBLIC_IP>:18789` or the explicitly reviewed wildcard address. If it
is wider than intended, stop the service before generating a QR.

## 7. VPS systemd compatibility overrides

Validate the installed unit before relying on it:

```bash
systemd-analyze --user verify ~/.config/systemd/user/r1-hermes.service
journalctl --user-unit r1-hermes.service --since today -o cat
```

Some VPS images reject user-service sandboxing directives such as `ProtectHome=tmpfs`, mount
namespacing, or syscall filters. Prefer the packaged hardened unit when it works. If the host
rejects a directive, use the narrowest local override that lets the service start; do not remove the
env file, do not move the gateway token into `ExecStart`, and do not make all of `$HOME` writable.

For hosts that cannot use `ProtectHome=tmpfs`, this fallback keeps home read-only and makes only the
state, runtime, and Hermes home paths writable:

```bash
systemctl --user edit r1-hermes.service
```

```ini
[Service]
ProtectHome=read-only
ReadWritePaths=%S/r1-hermes %t/r1-hermes %h/.hermes
```

If the host rejects namespace restriction for user services, clear only that setting in a drop-in:

```ini
[Service]
RestrictNamespaces=false
```

If an older systemd build rejects the syscall filter syntax, clear the filter in a local drop-in and
keep the rest of the sandbox intact:

```ini
[Service]
SystemCallFilter=
```

After any override, re-run verification, restart, and probe. Treat a service that starts only after
removing broad sandboxing as a deployment exception that needs host-specific documentation.

## 8. Verify WSS before QR generation

Check the certificate chain and expiry from the gateway host:

```bash
openssl s_client -connect <R1_HOSTNAME>:18789 -servername <R1_HOSTNAME> </dev/null \
  | openssl x509 -noout -subject -issuer -dates
openssl x509 -checkend 2592000 -noout \
  -in /etc/r1-hermes/tls/<R1_HOSTNAME>/fullchain.pem
```

The `-checkend 2592000` command exits successfully only when the certificate is valid for at least
30 more days.

Probe the exact public WSS URL from a client path that should be allowed. Source the env file in a
private shell so the probe can read the gateway token without printing it:

```bash
set -a
. ~/.config/r1-hermes/r1-hermes.env
set +a
r1-hermes probe --url wss://<R1_HOSTNAME>:18789/ --message 'Reply with OK from Hermes'
```

From an untrusted network, verify the endpoint either cannot connect, is blocked by firewall, or
fails authentication. Do not enable remote `/healthz` just for public checks; the default health
endpoint is intentionally local-only.

## 9. Generate and handle the QR PNG

Generate the QR only after TLS, allowlisting, and probe checks pass. The QR contains a bearer
secret, so do not print the payload JSON and do not take screenshots of it for public tickets.

```bash
r1-hermes qr \
  --host <R1_HOSTNAME> \
  --port 18789 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Scan it only with the intended Rabbit R1. Delete the PNG after successful pairing:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

If the QR PNG, gateway token, or payload JSON may have been exposed, stop or firewall the service,
run `r1-hermes rotate` against the private env file and state directory, restart, probe, and create
a fresh QR.

## 10. Renewal and expiry checks

Run a renewal dry run after the first certificate is issued:

```bash
sudo certbot renew --dry-run
```

Native TLS loads the certificate when `r1-hermes` starts. After a successful real renewal, copy the
renewed files into the service-readable directory, restart the user service, and probe again:

```bash
sudo install -m 0640 -o root -g r1-hermes-certs \
  /etc/letsencrypt/live/<R1_HOSTNAME>/fullchain.pem \
  /etc/r1-hermes/tls/<R1_HOSTNAME>/fullchain.pem
sudo install -m 0640 -o root -g r1-hermes-certs \
  /etc/letsencrypt/live/<R1_HOSTNAME>/privkey.pem \
  /etc/r1-hermes/tls/<R1_HOSTNAME>/privkey.pem
systemctl --user restart r1-hermes.service
r1-hermes probe --url wss://<R1_HOSTNAME>:18789/ --message 'Reply with OK from Hermes'
```

For an unattended renewal hook, keep the hook root-owned, copy only certificate files, and keep the
gateway token out of the hook. The hook may restart the user service only if your VPS supports a
reliable, documented way to reach that user's systemd manager. Otherwise, alert on expiry and run
the restart step manually.

Use these checks in monitoring or a maintenance calendar:

```bash
sudo certbot certificates -d <R1_HOSTNAME>
openssl x509 -checkend 1209600 -noout \
  -in /etc/r1-hermes/tls/<R1_HOSTNAME>/fullchain.pem
journalctl --user-unit r1-hermes.service --since today -o cat | grep '"event"'
```

`-checkend 1209600` warns when less than 14 days remain. Investigate renewal before the certificate
expires; do not replace a trusted certificate with a self-signed one for Rabbit R1.

## 11. Rollback

For a non-secret deployment rollback, stop public exposure first, then return to a private boundary:

```bash
systemctl --user stop r1-hermes.service
sudo ufw delete allow 18789/tcp
$EDITOR ~/.config/r1-hermes/r1-hermes.env
systemctl --user start r1-hermes.service
r1-hermes probe --url ws://127.0.0.1:18789/ --message 'Reply with OK from Hermes'
```

Set the env file back to a private mode such as:

```ini
R1_HERMES_HOST=127.0.0.1
R1_HERMES_PORT=18789
```

Remove or comment the native TLS paths only after the private replacement path is verified. If the
rollback involved a possible QR, gateway-token, device-token, env-file, or private-key exposure,
rotate the gateway token and revoke paired devices before reconnecting:

```bash
r1-hermes rotate \
  --state-dir ~/.local/state/r1-hermes \
  --env-file ~/.config/r1-hermes/r1-hermes.env
systemctl --user restart r1-hermes.service
```

For a certificate-only rollback, restore the previous copied files under `/etc/r1-hermes/tls`, keep
permissions at `0640` and group `r1-hermes-certs`, restart the service, and run the WSS probe. Do
not keep an expired certificate in service; return to Tailscale or another private path if a trusted
public certificate cannot be restored quickly.
