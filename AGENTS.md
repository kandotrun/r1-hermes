# AGENTS.md — r1-hermes developer handoff

This file is the first document an autonomous coding agent should read when working in this repository. It is intentionally more operational than the README: it explains what the project does, how to verify changes, where sensitive boundaries are, and how to pick up the current Rabbit R1 ↔ Hermes Agent work without rediscovering the same context.

## Project mission

`r1-hermes` is a hardened Rabbit R1/OpenClaw-compatible WebSocket gateway for Hermes Agent.

The core flow is:

1. Rabbit R1 scans a QR containing a gateway URL and a bearer token.
2. R1 connects over WebSocket or WebSocket-over-TLS.
3. The gateway authenticates `connect` / `gateway.connect` before doing any agent work.
4. Authenticated `chat.send` frames are normalized into a safe request object.
5. The gateway invokes `hermes chat` with the configured toolsets and returns chat lifecycle events to R1.

The project is security-first. Do not trade away authentication, secret hygiene, device allowlisting, rate/concurrency bounds, or log redaction for convenience.

## Current status snapshot

As of 2026-05-21:

- Text chat through Rabbit R1 is operational through the standalone `r1-hermes hermes` bridge.
- OpenClaw/Rabbit handshake variants `connect` and `gateway.connect` are supported.
- `chat.send` text extraction is implemented for common text fields and `content` items of type `text` / `input_text`.
- Known media-shaped content is recognized and rejected with safe `UNSUPPORTED_MEDIA` errors instead of being silently dropped; full camera/Vision ingestion is still pending.
- `chat.history` currently returns an explicit unsupported/empty history response.
- Device-token persistence uses keyed HMAC digests and has max-age/idle expiry.
- Public bind, high-impact Hermes toolsets, remote health diagnostics, and wildcard exposure are fail-closed unless explicitly opted into.
- Native TLS/WSS is supported with `--tls-cert-file` and `--tls-key-file`.
- Public native WSS deployment docs and Slack-equivalent toolset doctor checks have landed; current open camera/reliability backlog is issues #58, #59, #60, #61, #64, and #65.

Do not assume R1 camera photos are already passed to Hermes Vision. Confirm in `src/r1_hermes/payloads.py`, `src/r1_hermes/adapter.py`, and tests before claiming support.

## Non-negotiable security rules

- Treat these as secrets: `R1_HERMES_GATEWAY_TOKEN`, issued `deviceToken` values, raw QR payload JSON, QR PNG files, raw Rabbit R1 `device.id` values, `devices.json`, `device-token-hmac.key`, auth headers, and any captured media bytes.
- Never paste full tokens, raw QR payloads, raw device IDs, base64 images, auth headers, or raw captures into Slack, GitHub issues, PR bodies, logs, docs, or final summaries.
- If a real capture is needed, keep the raw file outside the repo, sanitize it with `r1_hermes.capture_sanitizer`, validate the sanitized fixture, and only commit the sanitized fixture.
- Bind local tests to `127.0.0.1`.
- Do not use `0.0.0.0` / `::` unless the human explicitly approves the network boundary and the code/config has `--allow-public-bind` or `R1_HERMES_ALLOW_PUBLIC_BIND=1`.
- For public Internet operation, require TLS/WSS, a strong gateway token, and `R1_HERMES_ALLOWED_DEVICE_IDS` after first pairing. Prefer private networking when possible.
- Keep default toolsets minimal (`safe`) unless the human explicitly authorizes more. High-impact toolsets (`terminal`, `file`, `browser`, `computer_use`, smart-home, vehicle controls, etc.) require `--allow-high-impact-toolsets` or `R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1` and a reviewed deployment boundary.
- Audit logs must include hashes, counts, sizes, codes, and durations only. They must not include prompt bodies, Hermes stderr, media bytes, raw tokens, raw device IDs, or raw run IDs.
- QR PNGs are bearer credentials. Deliver them only as a local path or secure native attachment, then delete them after pairing unless the human asks to keep them.

## Repository map

Important source files:

- `src/r1_hermes/cli.py` — CLI entrypoint and subcommands: `serve`, `hermes`, `payload`, `qr`, `revoke`, `rotate`, `cleanup`, `doctor`, `probe`.
- `src/r1_hermes/adapter.py` — hardened aiohttp WebSocket adapter, authentication, rate/concurrency limits, idempotency, chat lifecycle events, TLS config, health endpoint.
- `src/r1_hermes/payloads.py` — untrusted frame normalization into `ConnectRequest`, `ChatSendRequest`, and `ChatHistoryRequest`. This is the first place to change for media payload support.
- `src/r1_hermes/hermes_runner.py` — subprocess invocation of Hermes Agent.
- `src/r1_hermes/toolsets.py` — R1 toolset policy and high-impact gating.
- `src/r1_hermes/r1_client.py` — probe client used by tests and operators.
- `src/r1_hermes/capture_sanitizer.py` — converts private captures into commit-safe fixtures.
- `src/r1_hermes/native_gateway.py` — prototype native Hermes Gateway adapter path; not the main production path yet.
- `src/r1_hermes/audit.py` — structured redacted audit logging helpers.
- `src/r1_hermes/qr.py` — QR payload and PNG generation.
- `src/r1_hermes/chat_errors.py` — safe chat error classes.

Important docs:

- `README.md` — user-facing overview and install/run guide.
- `docs/agent-setup.md` — step-by-step autonomous setup and pairing guide.
- `docs/running.md` — runtime options, probes, deployment, audit logs.
- `docs/security.md` — security checklist and incident response.
- `docs/systemd-user-service.md` — user systemd service packaging and operations.
- `docs/capture-replay.md` — secret-safe capture sanitization/replay workflow.
- `docs/development-checks.md` — local security/release verification commands.

Important tests:

- `tests/test_payload_parser.py` — parser compatibility and validation.
- `tests/test_adapter_security.py` — auth, rate limits, idempotency, policy, lifecycle behavior.
- `tests/test_cli_probe.py` and `tests/test_cli_server_e2e.py` — CLI/probe/server behavior.
- `tests/test_hermes_runner.py` — Hermes subprocess behavior.
- `tests/test_doctor.py` — secret-safe diagnostics.
- `tests/test_capture_sanitizer.py` and `tests/test_fixture_replay.py` — capture sanitization and replay.
- `tests/test_native_tls.py` and `tests/test_systemd_packaging.py` — TLS and packaging/service safety.
- `tests/test_ci_security_checks.py` — CI/release safety checks.

## Development setup

Use a project-local virtualenv so the editable install points at this checkout.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,qr]'
python - <<'PY'
import r1_hermes
print(r1_hermes.__file__)
PY
```

The printed path must be inside this repository.

Useful commands:

```bash
r1-hermes --help
r1-hermes hermes --help
r1-hermes doctor --help
r1-hermes probe --help
```

Run the default test suite:

```bash
python -m pytest -q
```

Run targeted tests while iterating:

```bash
python -m pytest tests/test_payload_parser.py -q
python -m pytest tests/test_adapter_security.py -q
python -m pytest tests/test_hermes_runner.py -q
python -m pytest tests/test_doctor.py -q
python -m pytest tests/test_capture_sanitizer.py tests/test_fixture_replay.py -q
```

Run lint/security-oriented checks before PRs touching security, packaging, or release paths:

```bash
python -m ruff check src tests
python -m pytest tests/test_ci_security_checks.py -q
python -m pytest tests/test_deployment_docs.py tests/test_systemd_packaging.py -q
```

If `docs/development-checks.md` lists newer or stricter checks, follow that file.

## Local smoke-test flow

Generate a local token without printing it:

```bash
export R1_HERMES_GATEWAY_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
```

Check diagnostics:

```bash
r1-hermes doctor \
  --state-dir ~/.r1-hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --qr-output ./r1-hermes-secret.png
```

Start the local Hermes-backed gateway:

```bash
r1-hermes hermes \
  --host 127.0.0.1 \
  --port 18789 \
  --toolsets safe \
  --global-concurrency 2 \
  --per-device-concurrency 1 \
  --ready-file /tmp/r1-hermes.ready
```

Probe it from another shell with the same token:

```bash
r1-hermes probe \
  --url ws://127.0.0.1:18789/ \
  --message 'Reply with exactly OK'
```

To test the OpenClaw/Rabbit handshake variant:

```bash
r1-hermes probe \
  --url ws://127.0.0.1:18789/ \
  --connect-method gateway.connect \
  --message 'Reply with exactly OK'
```

The probe must not print gateway tokens or device tokens.

## Real-device deployment notes

Use `docs/agent-setup.md`, `docs/running.md`, and `docs/security.md` for the full runbook. Keep deployment-specific hostnames, IPs, tokens, QR images, and device IDs out of committed files.

A reviewed production-like setup may use:

- A user systemd service, usually `~/.config/systemd/user/r1-hermes.service`.
- A private env file such as `~/.config/r1-hermes/r1-hermes.env`.
- A state directory such as `~/.local/state/r1-hermes`.
- Native TLS cert/key paths under a private config directory.
- `--hermes-command` set to an absolute Hermes binary path when systemd cannot resolve `hermes` from `PATH`.
- `--no-continue` when the deployment should not assume a previous Hermes CLI session exists.
- `R1_HERMES_ALLOWED_DEVICE_IDS` pinned after first pairing.

For public WSS operation:

```bash
r1-hermes hermes \
  --host 0.0.0.0 \
  --port 18789 \
  --allow-public-bind \
  --tls-cert-file /path/to/fullchain.pem \
  --tls-key-file /path/to/privkey.pem \
  --allowed-device-id '<known-r1-device-id>' \
  --toolsets safe \
  --global-concurrency 2 \
  --per-device-concurrency 1
```

Only use high-impact toolsets if the human explicitly approved Slack-equivalent or similar permissions and the service sets the high-impact opt-in:

```bash
R1_HERMES_ALLOW_HIGH_IMPACT_TOOLSETS=1
r1-hermes hermes --toolsets browser,clarify,code_execution,computer_use,cronjob,delegation,file,image_gen,memory,messaging,session_search,skills,terminal,todo,tts,vision,web,x_search
```

The exact list may differ by Hermes installation. Prefer copying from the reviewed deployment config rather than inventing a list.

For VPS/container hosts, some systemd sandbox directives may fail with `status=218/CAPABILITIES`. If that happens, add a narrow user-service drop-in documenting why the directive was relaxed. Do not silently remove authentication, TLS, device allowlisting, or app-level limits to compensate.

## QR generation and handling

For private direct WebSocket:

```bash
r1-hermes qr \
  --host <reachable-private-host> \
  --port 18789 \
  --protocol ws \
  --output ./r1-hermes-secret.png
```

For TLS/WSS:

```bash
r1-hermes qr \
  --host <tls-hostname> \
  --port 18789 \
  --protocol wss \
  --output ./r1-hermes-secret.png
```

Rules:

- The QR protocol must match what R1 actually dials.
- Do not use `127.0.0.1` in a real-device QR unless the R1 client runs on the same host.
- Do not print payload JSON unless you are doing a private local debug and can guarantee it will not enter logs/chat.
- Delete the QR after pairing:

```bash
shred -u ./r1-hermes-secret.png 2>/dev/null || rm -f ./r1-hermes-secret.png
```

## Capture, media, and camera work

The next major feature area is R1 camera/media support. Work on it in small, testable stages.

Current parser limitation:

- `ChatSendRequest` only contains `message`, `session_key`, and `idempotency_key`.
- `_content_to_text` extracts text from supported text parts and raises `UNSUPPORTED_MEDIA` for known media-shaped content.
- Image/audio/media fields are recognized as unsupported today; they are not yet decoded, persisted, or passed through to Hermes Vision.
- Unknown R1 camera methods/shapes may still need safe real-device shape capture before implementation.

Preferred implementation order:

1. Preserve the current explicit `UNSUPPORTED_MEDIA` behavior while adding typed attachment metadata to `payloads.py`, for example source field, MIME type, size, filename/extension, and either a safe bytes handle or a decoded private temp file reference.
2. Extend parser tests with sanitized media fixtures before wiring Hermes Vision.
3. Store accepted images under the configured state directory with restrictive permissions and cleanup/TTL.
4. Pass images to Hermes as `MEDIA:/absolute/path` plus the user text so the existing Vision toolchain can consume them.
5. Add replay tests using sanitized real-device fixture shapes.
6. Confirm logs show only counts, sizes, hashes, and shape metadata, never raw media.

When capturing real-device media shapes:

- Start with shape-only diagnostics. Do not dump whole frames.
- If a raw frame is unavoidable, write it to a private file outside the repo with restrictive permissions.
- Immediately sanitize with `r1_hermes.capture_sanitizer`.
- Validate with `python -m r1_hermes.capture_sanitizer --check --input <fixture>`.
- Commit only the sanitized fixture and tests.

## Backlog and issue queue

The GitHub repo is `kandotrun/r1-hermes`. The active GitHub Project is user project `kandotrun` project number `2` titled `r1-hermes`.

Current open Todo issues include:

- #58 — Implement Rabbit R1 camera image ingestion for Hermes Vision.
- #59 — Pass R1 image attachments through to Hermes as MEDIA files.
- #60 — Add secret-safe frame-shape diagnostics for unknown Rabbit R1 media methods.
- #61 — Improve long-running R1 chat UX with configurable timeout and heartbeat events.
- #64 — Add reconnect-safe retry semantics for R1 sends during mobile network drops.
- #65 — Add sanitized real-device media fixture replay once R1 camera payload is captured.

Before starting work, check current state instead of trusting this snapshot:

```bash
gh issue list --repo kandotrun/r1-hermes --state open --limit 50
gh project item-list 2 --owner kandotrun --format json --limit 100
```

When creating new issues, use existing labels and add them to Project `Todo` if Symphony/agents are expected to pick them up.

## Coding rules and design constraints

- Parse untrusted frames defensively. Accept known aliases, reject malformed objects, and return safe `PayloadParseError` codes.
- Keep authentication before agent execution. No Hermes subprocess may start before successful connect auth.
- Keep idempotency semantics per device and session. Duplicate sends must not spawn duplicate Hermes subprocesses.
- Keep rate limits and concurrency checks before invoking Hermes.
- Preserve clean cancellation when a WebSocket disconnects.
- Keep `adapter.py` logs structured and redacted via `audit_log`.
- Use `Path` and configured `state_dir`; do not hardcode `/home/kan` or `~/.hermes` in library code.
- Do not add broad dependencies without need. This package is intentionally small; `aiohttp` is the only required runtime dependency, with QR/dev dependencies optional.
- Use feature flags/env opts for risky diagnostics. Default behavior should be quiet and safe.
- Ensure CLI options have matching env support where established by the codebase.
- Tests should cover both accepted aliases and rejection paths.

## Git and PR workflow

Before editing:

```bash
git status --short --branch
git fetch origin --prune
```

If there are unrelated local changes, do not overwrite them. Ask or isolate your work on a new branch.

Branch naming examples:

- `docs/update-agent-handoff`
- `feat/r1-media-parser`
- `fix/reconnect-idempotency`
- `test/media-fixture-replay`

Commit style:

```text
docs: update agent handoff
feat: parse R1 image attachments
fix: preserve idempotency across reconnects
test: add sanitized R1 media fixture replay
```

Before opening a PR, run the targeted tests for the area touched plus broader checks for security-sensitive changes. In PR bodies, mention security impact and tests run. Never include secrets or private deployment values in PR text.

## Verification checklist before reporting success

For docs-only changes:

- `git diff --check` passes.
- Links/commands were sanity-checked against current CLI help when practical.
- No secrets, real device IDs, raw host-specific config, QR payloads, or private paths were introduced.

For parser/media changes:

- `python -m pytest tests/test_payload_parser.py tests/test_capture_sanitizer.py tests/test_fixture_replay.py -q` passes.
- Sanitized fixtures validate.
- Tests prove raw media content is absent from logs/fixtures.

For adapter/auth/runtime changes:

- `python -m pytest tests/test_adapter_security.py tests/test_cli_server_e2e.py tests/test_cli_probe.py -q` passes.
- Auth-before-execution, rate limits, idempotency, cancellation, and safe errors are still covered.

For Hermes subprocess/toolset changes:

- `python -m pytest tests/test_hermes_runner.py tests/test_doctor.py -q` passes.
- High-impact toolset gating remains fail-closed.

For service/TLS/deployment changes:

- `python -m pytest tests/test_native_tls.py tests/test_systemd_packaging.py tests/test_deployment_docs.py -q` passes.
- Docs include rollback/rotation guidance without exposing real deployment secrets.

## Common troubleshooting

Hermes CLI missing:

- Verify `hermes --version`.
- Use `--hermes-command /absolute/path/to/hermes` under systemd if `PATH` is minimal.

Probe connects but chat fails:

- Check `journalctl --user -u r1-hermes --since '10 minutes ago' --no-pager` locally.
- Redact output before sharing.
- Look for `chat.parser_error`, `chat.run_error`, `hermes.subprocess_timeout`, and `busy_rejected`.

Wildcard bind rejected:

- Use a concrete host or add `--allow-public-bind` only after explicit approval.

Device pairs once but reconnect fails:

- Check `R1_HERMES_ALLOWED_DEVICE_IDS`, device-token expiry, `devices.json` permissions, and whether `rotate`/`revoke` cleared the token.

Long-running tool calls timeout:

- Check `--timeout` and whether the R1 UX needs heartbeat/progress events. See issue #61.

Camera/photo send appears to do nothing:

- Confirm whether the R1 actually sent a WebSocket frame.
- Inspect only safe shape logs or sanitized captures.
- Work from issues #58, #59, #60, and #65.

## Final reporting style

When reporting back to Kan or another operator:

- State exactly what changed and what was verified.
- Include issue/PR numbers and commands run.
- Mention any remaining uncommitted changes or local-only operational patches.
- Do not include secrets, raw IDs, QR payloads, or raw captures.
- Keep Slack replies concise and avoid Markdown tables.
