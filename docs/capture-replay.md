# Secret-safe capture replay workflow

Use this workflow when a maintainer has a private Rabbit R1/OpenClaw WebSocket capture and wants
to turn the observed frame shapes into public replay fixtures. Captures may contain gateway tokens,
issued device tokens, raw device IDs, personal prompts, assistant text, network addresses, and
timestamps. Treat the raw file as a secret until it has been sanitized and validated.

## One-command sanitizer

Run the sanitizer from the repository root. Keep the raw capture outside the repo, and pass every
known private value with `--forbid` so validation fails if a value survives redaction.

```bash
python -m r1_hermes.capture_sanitizer \
  --input /path/to/private-r1-capture.json \
  --output tests/fixtures/r1_payloads/<short-shape-name>.json \
  --forbid "$R1_HERMES_GATEWAY_TOKEN" \
  --forbid '<issued device token if known>' \
  --forbid '<raw device id if known>' \
  --forbid '<private prompt text if known>'
```

The command accepts either one frame object, an array of frame objects, or an object with a
top-level `frames` array. It writes pretty JSON with owner-only file permissions, refuses to
overwrite existing output unless `--overwrite` is set, and does not print raw frame contents.

To validate an existing fixture without rewriting it:

```bash
python -m r1_hermes.capture_sanitizer \
  --check \
  --input tests/fixtures/r1_payloads/<short-shape-name>.json
```

## What gets redacted

The sanitizer preserves the public frame shape but replaces sensitive or high-entropy details with
stable dummy values:

- gateway auth fields such as `token`, `authToken`, `gatewayToken`, and `bearerToken` become
  `DUMMY_GATEWAY_TOKEN_DO_NOT_USE`.
- issued device auth fields such as `deviceToken` become `DUMMY_DEVICE_TOKEN_DO_NOT_USE`.
- device IDs, serials, and device-string aliases become `r1-sanitized-device` style aliases.
- prompt, input, body, message text, and assistant text become fixed sanitized fixture text.
- run IDs, frame IDs, and session IDs become deterministic fixture aliases.
- host, URL, and IP fields become documentation-safe addresses such as `100.64.0.10` or
  `192.0.2.10`.
- binary/audio/image blobs under data-like fields become `DUMMY_BINARY_DATA_OMITTED`.
- raw authorization header fields become `[REDACTED]`.

The validator then checks the sanitized JSON against the replay schema for QR payloads, `connect`,
`gateway.connect`, `chat.send`, response, ack, and chat event frames. This makes future Rabbit or
OpenClaw shape changes visible in review while keeping private data out of git.

## Commit checklist

Before committing a new capture fixture:

- Do not paste raw captures, QR payloads, auth headers, gateway tokens, device tokens, device IDs,
  or personal prompts into public issues, pull requests, logs, screenshots, or chat.
- Keep the private capture outside the repository, ideally in a temporary directory with restrictive
  permissions, and delete it after the fixture is reviewed.
- Run the sanitizer with `--forbid` for every known secret or private string from the capture.
- Run `python -m r1_hermes.capture_sanitizer --check --input <fixture>` on the sanitized fixture.
- Add or update replay tests in `tests/test_fixture_replay.py` or parser tests in
  `tests/test_payload_parser.py` when the capture introduces a new `connect`, `gateway.connect`,
  `chat.send`, ack, or event shape.
- Run `python -m pytest -q`, `python -m ruff check .`, and
  `python -m compileall -q src tests` before opening a PR.
